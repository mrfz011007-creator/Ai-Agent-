from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.plan import PlanDecoder, PlanGraphError, PlanProposal


PLAN_SCHEMA_INSTRUCTION = """Return ONLY a JSON object with this shape:
{"goal":"string","tasks":[{"task_id":"string","title":"string","dependencies":["task_id"],"execution_contract":{"objective":"string","allowed_tools":["tool"],"allowed_capabilities":["workspace.read"],"max_tool_calls":10,"retry_limit":0,"evidence_required":true,"completion_conditions":[{"type":"evidence_success","task_id":"task_id"}]}}],"acceptance_criteria":[{"type":"all_tasks_completed"}]}
Machine-verifiable criterion types are: task_completed, evidence_success, tool_success, artifact_exists, artifact_kind, all_tasks_completed, no_failed_tasks.
Every task must include an execution_contract with structured completion_conditions. Use only the capabilities actually required. Do not emit natural-language completion conditions or acceptance criteria; they are rejected.
Do not include markdown, executable commands, shell syntax, or secrets.
Create a bounded dependency graph. Task descriptions express intent only; execution is authorized separately by the runtime."""


@dataclass
class ModelPlanService:
    """Turns untrusted model text into a deterministic, validated plan proposal."""

    model_call: Callable[[str], str]

    def propose(self, goal: str) -> PlanProposal:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Goal cannot be empty")
        raw = self.model_call(f"{PLAN_SCHEMA_INSTRUCTION}\n\nUSER GOAL:\n{goal.strip()}")
        if not isinstance(raw, str):
            raise PlanGraphError("Model planner must return text")
        try:
            payload: Mapping[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanGraphError("Model planner returned invalid JSON") from exc
        return PlanDecoder.from_mapping(payload)
