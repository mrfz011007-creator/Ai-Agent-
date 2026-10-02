from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Capability(str, Enum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    PROCESS_EXECUTE = "process.execute"
    ARTIFACT_CREATE = "artifact.create"
    NETWORK_READ = "network.read"
    NETWORK_WRITE = "network.write"
    CREDENTIAL_USE = "credential.use"


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    reason: str


class CapabilityPolicy:
    """Maps registered tools to explicit capabilities."""

    def __init__(self, registry_getter):
        self._registry_getter = registry_getter

    def decide(self, tool: str, metadata: dict | None = None) -> CapabilityDecision:
        metadata = metadata if metadata is not None else self._registry_getter(tool)
        if metadata is None:
            return CapabilityDecision(False, f"Unknown tool: {tool}")

        raw = metadata.get("capabilities", ())
        if isinstance(raw, str):
            raw = (raw,)
        try:
            capabilities = tuple(Capability(value) for value in raw)
        except ValueError as error:
            return CapabilityDecision(False, f"Unknown capability: {error}")

        if not capabilities:
            return CapabilityDecision(False, f"Tool has no declared capabilities: {tool}")

        return CapabilityDecision(True, "Capabilities declared")


def capabilities_for_tool(metadata: dict) -> tuple[Capability, ...]:
    raw = metadata.get("capabilities", ())
    if isinstance(raw, str):
        raw = (raw,)
    return tuple(Capability(value) for value in raw)
