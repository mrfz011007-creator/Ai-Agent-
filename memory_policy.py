from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

MEMORY_TYPES = frozenset({"fact", "decision", "experience", "preference"})
RETENTION_POLICIES = frozenset({"normal", "durable", "ephemeral"})


@dataclass(frozen=True)
class MemoryDecision:
    allowed: bool
    reason: str
    importance: float
    confidence: float
    retention: str


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _has_strong_source(source: Mapping[str, Any] | None) -> bool:
    if not source:
        return False
    kind = str(source.get("kind", "")).lower()
    ref = str(source.get("ref", "")).strip()
    return bool(ref) and kind not in {"unknown", "model", "llm"}


def evaluate_memory_candidate(
    *,
    memory_type: str,
    key: str,
    value: Any,
    source: Mapping[str, Any] | None,
    provenance: Mapping[str, Any] | None,
    context: Mapping[str, Any] | None,
    importance: float | None = None,
    confidence: float | None = None,
    retention: str = "normal",
    evidence_refs: list[str] | None = None,
) -> MemoryDecision:
    if memory_type not in MEMORY_TYPES:
        return MemoryDecision(False, "MEMORY_TYPE_INVALID", 0.0, 0.0, retention)
    if not isinstance(key, str) or not key.strip():
        return MemoryDecision(False, "MEMORY_KEY_INVALID", 0.0, 0.0, retention)
    if value is None:
        return MemoryDecision(False, "MEMORY_VALUE_EMPTY", 0.0, 0.0, retention)
    if retention not in RETENTION_POLICIES:
        return MemoryDecision(False, "MEMORY_RETENTION_INVALID", 0.0, 0.0, retention)

    evidence_count = len(evidence_refs or [])
    has_source = _has_strong_source(source)
    has_provenance = bool(provenance)
    has_context = bool(context)
    auto_confidence = 0.35
    auto_confidence += 0.30 if has_source else 0.0
    auto_confidence += 0.20 if evidence_count else 0.0
    auto_confidence += 0.15 if has_provenance else 0.0
    auto_confidence = _clamp(auto_confidence)
    final_confidence = _clamp(auto_confidence if confidence is None else confidence)

    auto_importance = 0.25
    auto_importance += 0.20 if memory_type in {"fact", "decision"} else 0.10
    auto_importance += 0.15 if has_context else 0.0
    auto_importance += 0.20 if evidence_count else 0.0
    auto_importance += 0.20 if retention == "durable" else (-0.10 if retention == "ephemeral" else 0.0)
    auto_importance = _clamp(auto_importance)
    final_importance = _clamp(auto_importance if importance is None else importance)

    if final_confidence < 0.35:
        return MemoryDecision(False, "MEMORY_CONFIDENCE_TOO_LOW", final_importance, final_confidence, retention)
    if final_importance < 0.20:
        return MemoryDecision(False, "MEMORY_IMPORTANCE_TOO_LOW", final_importance, final_confidence, retention)

    return MemoryDecision(True, "MEMORY_ACCEPTED", final_importance, final_confidence, retention)
