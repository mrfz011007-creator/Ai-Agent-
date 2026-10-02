from __future__ import annotations

from collections.abc import Mapping, Sequence



class CriterionValidationError(ValueError):
    """Raised when a machine-verifiable criterion is malformed."""


SUPPORTED_CRITERIA = {
    "task_completed",
    "evidence_success",
    "tool_success",
    "artifact_exists",
    "artifact_kind",
    "all_tasks_completed",
    "no_failed_tasks",
}


def validate_criterion(value: Mapping) -> dict:
    if not isinstance(value, Mapping):
        raise CriterionValidationError("Criterion must be an object")

    kind = value.get("type")
    if kind not in SUPPORTED_CRITERIA:
        raise CriterionValidationError(f"Unsupported criterion type: {kind!r}")

    normalized = dict(value)
    if kind in {"task_completed", "evidence_success", "tool_success", "artifact_exists", "artifact_kind"}:
        if not isinstance(value.get("task_id"), str) or not value["task_id"].strip():
            raise CriterionValidationError(f"{kind} requires task_id")

    if kind == "evidence_success":
        if "evidence_id" in value and not isinstance(value["evidence_id"], str):
            raise CriterionValidationError("evidence_id must be a string")
    if kind == "tool_success":
        if "tool" in value and not isinstance(value["tool"], str):
            raise CriterionValidationError("tool must be a string")
    if kind == "artifact_kind":
        if not isinstance(value.get("kind"), str) or not value["kind"].strip():
            raise CriterionValidationError("artifact_kind requires kind")
    return normalized


def validate_criteria(criteria: Sequence[Mapping]) -> tuple[dict, ...]:
    if not isinstance(criteria, (list, tuple)):
        raise CriterionValidationError("Criteria must be a list")
    if not criteria:
        return ({"type": "all_tasks_completed"},)
    return tuple(validate_criterion(item) for item in criteria)


class CriteriaEvaluator:
    """Evaluates only a finite, machine-verifiable criterion language."""

    def __init__(self, evidence_store, artifact_manager):
        self.evidence_store = evidence_store
        self.artifact_manager = artifact_manager

    def evaluate_task_conditions(
        self,
        *,
        task_id: str,
        evidence_ids: tuple[str, ...],
        conditions: Sequence[Mapping],
        expected_attempt_id: str | None = None,
    ) -> tuple[bool, str, tuple[str, ...]]:
        if not conditions:
            return False, "No machine-verifiable completion conditions supplied", ()

        normalized = validate_criteria(conditions)
        task_evidence = [
            self.evidence_store.get(evidence_id)
            for evidence_id in evidence_ids
        ]
        task_evidence = [item for item in task_evidence if item is not None]
        if expected_attempt_id is not None:
            task_evidence = [
                item for item in task_evidence
                if item.attempt_id == expected_attempt_id
            ]

        for condition in normalized:
            ok, reason = self._evaluate_condition(
                condition,
                task_id=task_id,
                task_evidence=task_evidence,
                all_task_ids=(task_id,),
                completed_task_ids=(),
                failed_task_ids=(),
            )
            if not ok:
                return False, reason, tuple(item.evidence_id for item in task_evidence)
        return True, "All completion conditions passed", tuple(
            item.evidence_id for item in task_evidence
        )

    def evaluate_plan_criteria(
        self,
        *,
        criteria: Sequence[Mapping],
        task_ids: Sequence[str],
        completed_task_ids: Sequence[str],
        failed_task_ids: Sequence[str] = (),
    ) -> tuple[bool, str, tuple[str, ...]]:
        normalized = validate_criteria(criteria)
        evidence_ids: list[str] = []
        for criterion in normalized:
            task_id = criterion.get("task_id")
            task_evidence = []
            if task_id:
                for row in self.evidence_store.store.load_evidence_for_task(task_id):
                    evidence = self.evidence_store.get(row["evidence_id"])
                    if evidence is not None:
                        task_evidence.append(evidence)
                        if evidence.evidence_id not in evidence_ids:
                            evidence_ids.append(evidence.evidence_id)

            ok, reason = self._evaluate_condition(
                criterion,
                task_id=task_id,
                task_evidence=task_evidence,
                all_task_ids=tuple(task_ids),
                completed_task_ids=tuple(completed_task_ids),
                failed_task_ids=tuple(failed_task_ids),
            )
            if not ok:
                return False, reason, tuple(evidence_ids)
        return True, "All acceptance criteria passed", tuple(evidence_ids)

    def _evaluate_condition(
        self,
        condition: Mapping,
        *,
        task_id: str | None,
        task_evidence,
        all_task_ids: Sequence[str],
        completed_task_ids: Sequence[str],
        failed_task_ids: Sequence[str],
    ) -> tuple[bool, str]:
        kind = condition["type"]

        if kind == "all_tasks_completed":
            ok = bool(all_task_ids) and set(all_task_ids) == set(completed_task_ids)
            return ok, "Not all tasks are completed" if not ok else "All tasks completed"

        if kind == "no_failed_tasks":
            ok = not failed_task_ids
            return ok, "A task failed" if not ok else "No failed tasks"

        if task_id not in all_task_ids:
            return False, f"Unknown criterion task: {task_id}"

        if kind == "task_completed":
            ok = task_id in completed_task_ids
            return ok, f"Task {task_id} is not completed" if not ok else f"Task {task_id} completed"

        if kind == "evidence_success":
            wanted = condition.get("evidence_id")
            matches = [
                evidence for evidence in task_evidence
                if evidence.success and (wanted is None or evidence.evidence_id == wanted)
            ]
            ok = bool(matches)
            return ok, f"No successful evidence for task {task_id}" if not ok else "Successful evidence verified"

        if kind == "tool_success":
            wanted = condition.get("tool")
            matches = [
                evidence for evidence in task_evidence
                if evidence.success and (wanted is None or evidence.tool == wanted)
            ]
            ok = bool(matches)
            return ok, f"No successful {wanted or 'tool'} evidence for task {task_id}" if not ok else "Tool success verified"

        artifacts = [
            artifact for artifact_id in self._artifact_ids_for_task(task_id)
            if (artifact := self.artifact_manager.get(artifact_id)) is not None
        ]
        if kind == "artifact_exists":
            ok = any(self.artifact_manager.verify(a.artifact_id, task_id=task_id)[0] for a in artifacts)
            return ok, f"No valid artifact for task {task_id}" if not ok else "Artifact verified"

        if kind == "artifact_kind":
            wanted = condition["kind"]
            ok = any(
                a.kind == wanted and self.artifact_manager.verify(a.artifact_id, task_id=task_id)[0]
                for a in artifacts
            )
            return ok, f"No valid artifact of kind {wanted} for task {task_id}" if not ok else "Artifact kind verified"

        raise CriterionValidationError(f"Unsupported criterion type: {kind}")

    def _artifact_ids_for_task(self, task_id: str | None) -> tuple[str, ...]:
        if not task_id:
            return ()
        rows = self.artifact_manager.store.load_artifacts_for_task(task_id)
        return tuple(row["artifact_id"] for row in rows)
