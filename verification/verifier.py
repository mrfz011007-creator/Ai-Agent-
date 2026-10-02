from __future__ import annotations

from core.contracts import VerificationResult, VerificationStatus
from verification.evidence import EvidenceStore


class Verifier:
    """Completion authority. Tool success alone never completes a task."""

    def __init__(self, evidence_store: EvidenceStore):
        self.evidence_store = evidence_store

    def verify_evidence(self, evidence_id: str) -> VerificationResult:
        evidence = self.evidence_store.get(evidence_id)
        if evidence is None:
            return VerificationResult(
                VerificationStatus.UNKNOWN,
                "Evidence not found",
                (evidence_id,),
            )

        if evidence.success:
            return VerificationResult(
                VerificationStatus.PASSED,
                "Evidence confirms successful tool execution",
                (evidence_id,),
            )

        return VerificationResult(
            VerificationStatus.FAILED,
            evidence.error or "Tool execution failed",
            (evidence_id,),
        )
