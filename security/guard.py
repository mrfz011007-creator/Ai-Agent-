from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str


_DESTRUCTIVE_EXECUTABLES = {
    "rm",
    "rmdir",
    "dd",
    "mkfs",
    "mkfs.ext4",
    "shutdown",
    "reboot",
    "poweroff",
    "halt",
    "kill",
    "killall",
    "pkill",
}


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

            executable = Path(argv[0]).name.lower()
            if executable in _DESTRUCTIVE_EXECUTABLES:
                return GuardResult(False, "DESTRUCTIVE_COMMAND_DENIED")

            if executable == "git" and self._destructive_git(argv[1:]):
                return GuardResult(False, "DESTRUCTIVE_GIT_COMMAND_DENIED")

        return GuardResult(True, "GUARD_ALLOWED")

    @staticmethod
    def _destructive_git(args: list[str]) -> bool:
        if not args:
            return False
        if args[0] == "reset" and "--hard" in args:
            return True
        if args[0] == "clean" and any(flag.startswith("-") and "f" in flag for flag in args):
            return True
        if args[0] == "checkout" and "--" in args:
            return True
        return False

    def _inside_workspace(self, path: Path) -> bool:
        try:
            path.relative_to(self.workspace_root)
            return True
        except ValueError:
            return False
