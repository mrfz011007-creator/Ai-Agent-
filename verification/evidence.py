from __future__ import annotations

from dataclasses import dataclass, field

from core.contracts import Evidence, ToolResult


@dataclass
class EvidenceStore:
    records: dict[str, Evidence] = field(default_factory=dict)

    def record(
        self,
        *,
        evidence_id: str,
        task_id: str | None,
        tool: str,
        action: str,
        result: ToolResult,
    ) -> Evidence:
        if evidence_id in self.records:
            raise ValueError(f"Evidence already exists: {evidence_id}")

        evidence = Evidence(
            evidence_id=evidence_id,
            task_id=task_id,
            tool=tool,
            action=action,
            success=result.success,
            result_status=result.status,
            error=result.error,
        )
        self.records[evidence_id] = evidence
        return evidence

    def get(self, evidence_id: str) -> Evidence | None:
        return self.records.get(evidence_id)
