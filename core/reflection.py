from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

MAX_EXPERIENCE_CHARS = 12000
MAX_CONTEXT_CHARS = 12000
MAX_CANDIDATES = 3
ALLOWED_TYPES = frozenset({'fact', 'decision', 'experience', 'preference'})
ALLOWED_RETENTION = frozenset({'normal', 'durable', 'ephemeral'})

@dataclass(frozen=True)
class ReflectionCandidate:
    key: str
    value: Any
    memory_type: str
    summary: str
    confidence: float | None
    importance: float | None
    retention: str
    evidence_refs: tuple[str, ...]
    tags: tuple[str, ...]
    scope: str
    risk: str

class ReflectionError(ValueError):
    pass

def _score(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError) as error:
        raise ReflectionError('REFLECTION_SCORE_INVALID') from error

def _parse_json(raw: str) -> Mapping[str, Any]:
    text = str(raw).strip()
    if text.startswith('```'):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip() == '```':
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ReflectionError('REFLECTION_OUTPUT_INVALID_JSON') from error
    if not isinstance(value, Mapping):
        raise ReflectionError('REFLECTION_OUTPUT_NOT_OBJECT')
    return value

def _risk(memory_type: str, scope: str, key: str) -> str:
    text = (memory_type + ' ' + scope + ' ' + key).lower()
    if any(x in text for x in ('permission', 'policy', 'capability', 'security', 'credential', 'authorization')):
        return 'high'
    if scope == 'general' or memory_type == 'decision':
        return 'medium'
    return 'low'

def validate_candidates(payload: Mapping[str, Any], *, allowed_evidence_refs: set[str]) -> list[ReflectionCandidate]:
    raw = payload.get('candidates', [])
    if not isinstance(raw, list):
        raise ReflectionError('REFLECTION_CANDIDATES_NOT_LIST')
    result = []
    seen = set()
    for item in raw[:MAX_CANDIDATES]:
        if not isinstance(item, Mapping):
            continue
        key = str(item.get('key', '')).strip()
        kind = str(item.get('memory_type', 'experience')).strip().lower()
        value = item.get('value')
        refs = item.get('evidence_refs', [])
        if not key or kind not in ALLOWED_TYPES or value is None or not isinstance(refs, list):
            continue
        refs = tuple(dict.fromkeys(str(x) for x in refs if str(x).strip()))
        if not refs or not set(refs).issubset(allowed_evidence_refs):
            continue
        retention = str(item.get('retention', 'normal')).lower()
        if retention not in ALLOWED_RETENTION:
            continue
        scope = str(item.get('scope', 'task')).lower()
        if scope not in {'task', 'project', 'environment', 'general'}:
            scope = 'task'
        identity = (kind, key)
        if identity in seen:
            continue
        seen.add(identity)
        floor = _risk(kind, scope, key)
        declared = str(item.get('risk', floor)).lower()
        if declared not in {'low', 'medium', 'high'}:
            declared = floor
        if floor == 'high' or (floor == 'medium' and declared == 'low'):
            declared = floor
        confidence = None if item.get('confidence') is None else _score(item.get('confidence'))
        importance = None if item.get('importance') is None else _score(item.get('importance'))
        tags = item.get('tags', [])
        tags = tuple(dict.fromkeys(str(x).strip() for x in tags if str(x).strip())) if isinstance(tags, list) else ()
        result.append(ReflectionCandidate(key, value, kind, str(item.get('summary', str(value))).strip()[:500],
            confidence, importance, retention, refs, tags, scope, declared))
    return result

class ReflectionService:
    def __init__(self, model_call: Callable[..., str], *, max_candidates: int = MAX_CANDIDATES):
        if max_candidates < 1:
            raise ValueError('max_candidates must be positive')
        self.model_call = model_call
        self.max_candidates = min(max_candidates, MAX_CANDIDATES)

    def reflect(self, *, experience: Mapping[str, Any], evidence_refs: list[str] | tuple[str, ...],
                related_memory: list[Mapping[str, Any]] | None = None) -> list[ReflectionCandidate]:
        evidence = tuple(dict.fromkeys(str(x) for x in evidence_refs if str(x).strip()))
        if not evidence:
            return []
        prompt = (
            'You are a bounded reflection component. Analyze the supplied execution experience.\n'
            'Return ONLY JSON with a candidates array. Each candidate needs key, value, memory_type, summary, confidence, importance, retention, evidence_refs, tags, scope, risk.\n'
            'Use only supplied evidence. Never invent evidence IDs. Every candidate must cite supplied evidence. Prefer narrow claims. '
            'Never create policy, permission, capability, credential, or security changes. Never execute tools. Reflection is proposal only. '
            'Return no candidates when there is no durable reusable knowledge.\n'
            'Evidence IDs: ' + json.dumps(evidence, ensure_ascii=False) + '\n'
            'Experience: ' + json.dumps(dict(experience), ensure_ascii=False, default=str)[:MAX_EXPERIENCE_CHARS] + '\n'
            'Related memory: ' + json.dumps(related_memory or [], ensure_ascii=False, default=str)[:MAX_CONTEXT_CHARS]
        )
        raw = self.model_call(prompt=prompt,
            system_instruction='Produce bounded memory candidates only. Never treat reflection as authority.',
            response_mime_type='application/json')
        return validate_candidates(_parse_json(raw), allowed_evidence_refs=set(evidence))[:self.max_candidates]