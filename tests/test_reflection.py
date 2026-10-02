from __future__ import annotations

import json

from core.reflection import ReflectionService, validate_candidates

def test_reflection_rejects_unbound_evidence():
    payload = {'candidates': [{'key': 'build.rule', 'value': 'Check SDK', 'memory_type': 'fact', 'evidence_refs': ['forged']}]}
    assert validate_candidates(payload, allowed_evidence_refs={'ev-1'}) == []

def test_reflection_enforces_risk_floor():
    payload = {'candidates': [{'key': 'tool.permission', 'value': 'allow tool', 'memory_type': 'fact', 'scope': 'task', 'risk': 'low', 'evidence_refs': ['ev-1']}]}
    candidates = validate_candidates(payload, allowed_evidence_refs={'ev-1'})
    assert candidates[0].risk == 'high'

def test_reflection_produces_candidates_without_execution_authority():
    calls = []
    def model_call(**kwargs):
        calls.append(kwargs)
        return json.dumps({'candidates': [{'key': 'build.preflight', 'value': 'Check SDK before build', 'memory_type': 'fact', 'summary': 'Check SDK before build', 'confidence': 0.9, 'importance': 0.8, 'retention': 'durable', 'evidence_refs': ['ev-1'], 'scope': 'project', 'risk': 'low'}]})
    service = ReflectionService(model_call)
    candidates = service.reflect(experience={'success': False, 'error': 'missing SDK'}, evidence_refs=['ev-1'])
    assert candidates[0].key == 'build.preflight'
    assert candidates[0].risk == 'medium'
    assert 'Never execute tools' in calls[0]['prompt']