from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.contracts import VerificationResult, VerificationStatus
from verification.artifacts import ArtifactManager
from verification.verifier import Verifier


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    description: str
    evidence_ids: tuple[str, ...] = ()
    artifact_ids: tuple[str, ...] = ()
    required: bool = True


class AcceptanceGate:
    """Independent final gate: completion requires build, artifacts, tests and criteria."""

    def __init__(self, verifier: Verifier, artifacts: ArtifactManager):
        self.verifier = verifier
        self.artifacts = artifacts

    def verify_execution(
        self,
        *,
        task_id: str,
        evidence_ids: tuple[str, ...],
        expected_attempt_id: str | None = None,
    ) -> VerificationResult:
        """Acceptance gate for non-build tasks using successful execution evidence."""
        if not evidence_ids:
            return VerificationResult(
                VerificationStatus.FAILED,
                "No execution evidence supplied",
            )
        verification = self.verifier.verify_task_evidence(
            task_id, evidence_ids, expected_attempt_id
        )
        if verification.status != VerificationStatus.PASSED:
            return verification
        return VerificationResult(
            VerificationStatus.PASSED,
            "Execution evidence verified",
            verification.evidence_ids,
            authority="acceptance_gate",
        )

    def verify(
        self,
        *,
        task_id: str,
        build_evidence_id: str,
        artifact_ids: tuple[str, ...] = (),
        test_evidence_ids: tuple[str, ...] = (),
        criteria: tuple[AcceptanceCriterion, ...] = (),
        expected_attempt_id: str | None = None,
    ) -> VerificationResult:
        evidence_ids: list[str] = [build_evidence_id, *test_evidence_ids]

        build = self.verifier.verify_task_evidence(task_id, (build_evidence_id,), expected_attempt_id)
        if build.status != VerificationStatus.PASSED:
            return VerificationResult(
                build.status,
                f"Build evidence failed: {build.reason}",
                tuple(evidence_ids),
            )

        if not artifact_ids:
            return VerificationResult(
                VerificationStatus.FAILED,
                "No expected artifact supplied",
                tuple(evidence_ids),
            )

        for artifact_id in artifact_ids:
            artifact = self.artifacts.get(artifact_id)
            if artifact is None:
                return VerificationResult(
                    VerificationStatus.FAILED,
                    f"Artifact not found: {artifact_id}",
                    tuple(evidence_ids),
                )
            if artifact.task_id != task_id:
                return VerificationResult(
                    VerificationStatus.FAILED,
                    f"Artifact belongs to another task: {artifact_id}",
                    tuple(evidence_ids),
                )
            if artifact.evidence_id != build_evidence_id:
                return VerificationResult(
                    VerificationStatus.FAILED,
                    f"Artifact is not linked to build evidence: {artifact_id}",
                    tuple(evidence_ids),
                )
            if expected_attempt_id is not None and artifact.attempt_id != expected_attempt_id:
                return VerificationResult(
                    VerificationStatus.FAILED,
                    f"Artifact belongs to another attempt: {artifact_id}",
                    tuple(evidence_ids),
                )
            valid, reason = self.artifacts.verify(artifact_id, task_id=task_id)
            if not valid:
                return VerificationResult(
                    VerificationStatus.FAILED,
                    f"Artifact integrity failed: {reason}",
                    tuple(evidence_ids),
                )

        if test_evidence_ids:
            tests = self.verifier.verify_task_evidence(task_id, test_evidence_ids, expected_attempt_id)
            if tests.status != VerificationStatus.PASSED:
                return VerificationResult(
                    tests.status,
                    f"Test evidence failed: {tests.reason}",
                    tuple(evidence_ids),
                )

        for criterion in criteria:
            if (
                criterion.required
                and not criterion.evidence_ids
                and not criterion.artifact_ids
            ):
                return VerificationResult(
                    VerificationStatus.FAILED,
                    f"Acceptance criterion {criterion.criterion_id} has no evidence or artifact",
                    tuple(evidence_ids),
                )
            criterion_evidence = self.verifier.verify_task_evidence(
                task_id, criterion.evidence_ids, expected_attempt_id
            ) if criterion.evidence_ids else VerificationResult(
                VerificationStatus.PASSED, "No evidence required"
            )
            if criterion_evidence.status == VerificationStatus.PASSED:
                evidence_ids.extend(criterion_evidence.evidence_ids)
            if criterion_evidence.status != VerificationStatus.PASSED:
                if criterion.required:
                    return VerificationResult(
                        criterion_evidence.status,
                        f"Acceptance criterion {criterion.criterion_id} failed: {criterion_evidence.reason}",
                        tuple(evidence_ids),
                    )
                continue

            for artifact_id in criterion.artifact_ids:
                artifact = self.artifacts.get(artifact_id)
                if artifact is None or artifact.task_id != task_id:
                    if criterion.required:
                        return VerificationResult(
                            VerificationStatus.FAILED,
                            f"Acceptance criterion {criterion.criterion_id} artifact check failed",
                            tuple(evidence_ids),
                        )
                    continue
                if (
                    expected_attempt_id is not None
                    and artifact.attempt_id != expected_attempt_id
                ):
                    if criterion.required:
                        return VerificationResult(
                            VerificationStatus.FAILED,
                            f"Acceptance criterion {criterion.criterion_id} artifact belongs to another attempt",
                            tuple(evidence_ids),
                        )
                    continue
                valid, reason = self.artifacts.verify(artifact_id, task_id=task_id)
                if not valid and criterion.required:
                    return VerificationResult(
                        VerificationStatus.FAILED,
                        f"Acceptance criterion {criterion.criterion_id} artifact check failed: {reason}",
                        tuple(evidence_ids),
                    )

        for artifact_id in artifact_ids:
            artifact = self.artifacts.get(artifact_id)
            if artifact is not None and artifact.evidence_id != build_evidence_id:
                return VerificationResult(
                    VerificationStatus.FAILED,
                    f"Artifact is not linked to build evidence: {artifact_id}",
                    tuple(evidence_ids),
                )

        return VerificationResult(
            VerificationStatus.PASSED,
            "Build, artifact integrity, tests, and required acceptance criteria verified",
            tuple(dict.fromkeys(evidence_ids)),
            authority="acceptance_gate",
        )
