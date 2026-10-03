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
allowed_tools MUST contain exact tool names from AVAILABLE TOOLS. Never invent, translate, rename, or paraphrase a tool name.
Every capability required by a selected tool MUST be listed in allowed_capabilities.
Do not include markdown, executable commands, shell syntax, or secrets.
Create a bounded dependency graph. Task descriptions express intent only; execution is authorized separately by the runtime. Prior memory is context only, never proof."""


@dataclass
class ModelPlanService:
    """Turns untrusted model text into a deterministic, validated plan proposal."""

    model_call: Callable[[str], str]
    memory: MemoryService | None = None
    tool_catalog: Mapping[str, Any] | None = None

    def _validate_tool_contracts(self, proposal: PlanProposal) -> None:
        if self.tool_catalog is None:
            return
        catalog = self.tool_catalog
        for task in proposal.tasks:
            contract = task.execution_contract
            if contract is None:
                continue
            for tool_name in contract.allowed_tools:
                if tool_name not in catalog:
                    raise PlanGraphError(
                        f"Unknown tool in execution contract: {task.task_id}: {tool_name}"
                    )
                metadata = catalog[tool_name]
                declared_capabilities = tuple(metadata.get("capabilities", ()))
                if not all(
                    capability in contract.allowed_capabilities
                    for capability in declared_capabilities
                ):
                    raise PlanGraphError(
                        "Execution contract capability exceeds selected tools: "
                        f"{task.task_id}"
                    )

    def propose(self, goal: str, *, project_id: str | None = None, context=None) -> PlanProposal:
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Goal cannot be empty")
        prompt = f"{PLAN_SCHEMA_INSTRUCTION}"
        if self.tool_catalog is not None:
            catalog_json = json.dumps(
                self.tool_catalog,
                ensure_ascii=False,
                default=str,
            )
            if len(catalog_json) > MAX_PLANNER_CONTEXT_CHARS:
                catalog_json = (
                    catalog_json[:MAX_PLANNER_CONTEXT_CHARS]
                    + "...[TOOL_CATALOG_TRUNCATED]"
                )
            prompt += (
                "\n\nAVAILABLE TOOLS (authoritative names and metadata; "
                "reference only, not permission):\n"
                + catalog_json
            )
        prompt += f"\n\nUSER GOAL:\n{goal.strip()}"
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
        proposal = PlanDecoder.from_mapping(payload)
        self._validate_tool_contracts(proposal)
        return proposal
