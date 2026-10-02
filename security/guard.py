from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str


class GuardEngine:
    """Concrete safety checks after permission and before execution."""

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()

    def check(self, *, tool: str, arguments: dict) -> GuardResult:
        cwd = arguments.get("cwd")
        if tool == "run_command":
            if not cwd:
                return GuardResult(False, "WORKSPACE_CWD_REQUIRED")
            try:
                resolved = Path(cwd).resolve()
            except OSError as error:
                return GuardResult(False, f"WORKSPACE_CWD_INVALID: {error}")
            if not self._inside_workspace(resolved):
                return GuardResult(False, "WORKSPACE_BOUNDARY_VIOLATION")

            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip():
                return GuardResult(False, "COMMAND_REQUIRED")
            try:
                argv = shlex.split(command)
            except ValueError as error:
                return GuardResult(False, f"COMMAND_PARSE_ERROR: {error}")
            if not argv:
                return GuardResult(False, "COMMAND_EMPTY")

        return GuardResult(True, "GUARD_ALLOWED")

    def _inside_workspace(self, path: Path) -> bool:
        try:
            path.relative_to(self.workspace_root)
            return True
        except ValueError:
            return False
