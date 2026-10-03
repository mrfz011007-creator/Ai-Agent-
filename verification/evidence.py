from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import Evidence, ToolResult
from core.state_store import StateStore


@dataclass
class EvidenceStore:
    store: StateStore | None = None
    records: dict[str, Evidence] = field(default_factory=dict)

    def record(
        self,
        *,
        evidence_id: str,
        task_id: str | None,
        tool: str,
        action: str,
        result: ToolResult,
        attempt_id: str | None = None,
        kind: str = "execution",
    ) -> Evidence:
        if kind != "execution":
            raise ValueError("Use record_reconciliation() for non-execution evidence")
        if evidence_id in self.records or (self.store and self.store.load_evidence(evidence_id)):
            raise ValueError(f"Evidence already exists: {evidence_id}")

        evidence = Evidence(
            evidence_id=evidence_id,
            task_id=task_id,
            attempt_id=attempt_id,
            tool=tool,
            action=action,
            success=result.success,
            result_status=result.status,
            error=result.error,
            kind=kind,
            authority="execution_router",
        )
        if self.store is not None:
            self.store.save_evidence(
                evidence_id=evidence.evidence_id,
                task_id=evidence.task_id,
                attempt_id=evidence.attempt_id,
                tool=evidence.tool,
                action=evidence.action,
                success=evidence.success,
                result_status=evidence.result_status,
                error=evidence.error,
                kind=evidence.kind,
                authority=evidence.authority,
                payload={
                    "evidence_id": evidence.evidence_id,
                    "task_id": evidence.task_id,
                    "tool": evidence.tool,
                    "action": evidence.action,
                    "success": evidence.success,
                    "result_status": evidence.result_status,
                    "error": evidence.error,
                    "kind": evidence.kind,
                },
            )
        self.records[evidence_id] = evidence
        return evidence


    def record_reconciliation(self, *, evidence_id: str, task_id: str, attempt_id: str,
                              tool: str, action: str, result: ToolResult) -> Evidence:
        if not task_id or not attempt_id:
            raise ValueError("Reconciliation evidence requires task and attempt IDs")
        if not result.success:
            raise ValueError("Reconciliation evidence must be successful")
        if evidence_id in self.records or (self.store and self.store.load_evidence(evidence_id)):
            raise ValueError(f"Evidence already exists: {evidence_id}")
        evidence = Evidence(
            evidence_id=evidence_id, task_id=task_id, attempt_id=attempt_id,
            tool=tool, action=action, success=True, result_status=result.status,
            error=result.error, kind="reconciliation", authority="reconciliation_boundary",
        )
        if self.store is not None:
            self.store.save_reconciliation_evidence(
                evidence_id=evidence.evidence_id,
                task_id=evidence.task_id,
                attempt_id=evidence.attempt_id,
                tool=evidence.tool,
                action=evidence.action,
                result_status=evidence.result_status,
                error=evidence.error,
                payload={"evidence_id": evidence.evidence_id, "task_id": evidence.task_id,
                         "tool": evidence.tool, "action": evidence.action, "success": True,
                         "result_status": evidence.result_status, "error": evidence.error,
                         "kind": evidence.kind, "authority": evidence.authority},
            )
        self.records[evidence_id] = evidence
        return evidence

    def get(self, evidence_id: str) -> Evidence | None:
        evidence = self.records.get(evidence_id)
        if evidence is not None:
            return evidence
        if self.store is None:
            return None
        row = self.store.load_evidence(evidence_id)
        if row is None:
            return None
        evidence = Evidence(
            evidence_id=row["evidence_id"],
            task_id=row["task_id"],
            attempt_id=row["attempt_id"],
            tool=row["tool"],
            action=row["action"],
            success=row["success"],
            result_status=row["result_status"],
            error=row["error"],
            kind=row["kind"],
            authority=row["authority"],
        )
        self.records[evidence_id] = evidence
        return evidence