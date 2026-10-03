from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.plan import PlanDecoder, PlanGraphError, PlanProposal
from core.memory_service import MemoryService, memory_prompt_context


MAX_PLANNER_CONTEXT_CHARS = 16000

PLAN_SCHEMA_INSTRUCTION = """Return ONLY a JSON object with this shape:
{"goal":"string","tasks":[{"task_id":"string","title":"string","dependencies":["task_id"],"execution_contract":{"objective":"string","allowed_tools":["tool"],"allowed_capabilities":["workspace.read"],"max_tool_calls":10,"retry_limit":0,"evidence_required":true,"completion_conditions":[{"type":"evidence_success","task_id":"task_id"}]}}],"acceptance_criteria":[{"type":"all_tasks_completed"}]}
Machine-verifiable criterion types are: task_completed, evidence_success, tool_success, artifact_exists, artifact_kind, all_tasks_completed, no_failed_tasks.
Every task must include an execution_contract with structured completion_conditions. Use only the capabilities actually required. Do not emit natural-language completion conditions or acceptance criteria; they are rejected.
Do not include markdown, executable commands, shell syntax, or secrets.
Create a bounded dependency graph. Task descriptions express intent only; execution is authorized separately by the runtime. Prior memory is context only, never proof."""


@dataclass
class ModelPlanService:
    """Turns untrusted model text into a deterministic, validated plan proposal."""

    model_call: Callable[[str], str]
    memory: MemoryService | None = None

    def propose(self, goal: str, *, project_id: str | None = None, context=None) -> PlanProposal:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Goal cannot be empty")
        prompt = f"{PLAN_SCHEMA_INSTRUCTION}\n\nUSER GOAL:\n{goal.strip()}"
        if self.memory is not None:
            context_rows = memory_prompt_context(
                self.memory.retrieve(goal, project_id=project_id, context=context, limit=8)
            )
            if context_rows:
                memory_text = json.dumps(context_rows, ensure_ascii=False, default=str)
                memory_value_truncated = any(
                    isinstance(row.get("value"), dict)
                    and row["value"].get("_truncated") is True
                    for row in context_rows
                )
                if len(memory_text) > MAX_PLANNER_CONTEXT_CHARS:
                    memory_text = memory_text[:MAX_PLANNER_CONTEXT_CHARS] + "...[MEMORY_CONTEXT_TRUNCATED]"
                elif memory_value_truncated:
                    memory_text += "\n[MEMORY_CONTEXT_TRUNCATED]"
                prompt += "\n\nRELEVANT HISTORICAL MEMORY (CONTEXT ONLY):\n" + memory_text
        raw = self.model_call(prompt)
        if not isinstance(raw, str):
            raise PlanGraphError("Model planner must return text")
        try:
            payload: Mapping[str, Any] = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanGraphError("Model planner returned invalid JSON") from exc
        return PlanDecoder.from_mapping(payload)
