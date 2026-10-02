from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.plan import PlanDecoder, PlanGraphError, PlanProposal


PLAN_SCHEMA_INSTRUCTION = """Return ONLY a JSON object with this shape:
{"goal":"string","tasks":[{"task_id":"string","title":"string","dependencies":["task_id"]}],"acceptance_criteria":["string"]}
Do not include markdown, executable commands, tool arguments, shell syntax, or secrets.
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
