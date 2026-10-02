from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core.contracts import Decision, PermissionDecision


@dataclass(frozen=True)
class PolicyContext:
    tool: str
    action: str
    arguments: Mapping[str, Any]
    source: str = "agent"
    task_id: str | None = None


class PolicyEngine:
    """Central authorization boundary.

    The model/tool never self-authorizes. Legacy registry permission metadata
    is interpreted here, while concrete safety checks can be added as guards.
    """

    def __init__(self, registry_getter):
        self._registry_getter = registry_getter

    def decide(self, context: PolicyContext) -> PermissionDecision:
        tool = self._registry_getter(context.tool)
        if tool is None:
            return PermissionDecision(
                Decision.DENY,
                f"Unknown tool: {context.tool}",
                "HIGH",
            )

        permission = tool.get("permission", "blocked")

        if permission == "blocked":
            return PermissionDecision(Decision.DENY, "Tool is blocked", "HIGH")

        if permission == "confirm":
            return PermissionDecision(
                Decision.ASK,
                "Tool requires explicit user confirmation",
                "MEDIUM",
            )

        if permission == "safe":
            return PermissionDecision(Decision.ALLOW, "Tool is permitted", "LOW")

        return PermissionDecision(
            Decision.DENY,
            f"Unknown permission policy: {permission}",
            "HIGH",
        )
