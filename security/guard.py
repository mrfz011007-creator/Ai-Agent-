from __future__ import annotations

import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

from execution.sandbox import find_trusted_system_executable, namespace_sandbox_status


@dataclass(frozen=True)
class GuardResult:
    allowed: bool
    reason: str


# Termux is not a hostile-code sandbox. Only small, non-interpreting utilities
# are permitted through the agent command tool until a real isolation backend
# is available.
_SAFE_EXECUTABLES = {"pwd", "ls", "whoami", "true", "false", "echo", "printf", "sleep"}
_DESTRUCTIVE_EXECUTABLES = {
    "rm", "rmdir", "dd", "mkfs", "fdisk", "parted", "wipefs", "shred",
    "chmod", "chown", "kill", "killall", "pkill", "reboot", "shutdown",
    "poweroff", "halt", "su", "sudo",
}
_MULTICALL_BINARIES = {"coreutils", "busybox", "toybox"}
_INTERPRETERS = {
    "python", "python2", "python3", "pypy", "pypy3", "node", "nodejs",
    "npm", "npx", "pnpm", "yarn", "ruby", "perl", "php", "lua",
    "luajit", "java", "javac", "jshell", "r", "rscript",
}
_SHELLS = {"sh", "bash", "dash", "zsh", "fish", "ksh", "csh", "tcsh"}
_LAUNCHERS = {"env", "busybox", "xargs", "nohup", "setsid", "timeout", "exec"}
_LS_FLAGS = {"-a", "-l", "-la", "-al", "-h", "-lah", "-alh", "--color=never"}


class GuardEngine:
    """Fail-closed command policy shared by all agent execution entry points."""

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).resolve()

    @staticmethod
    def _is_android() -> bool:
        return (
            os.environ.get("PREFIX", "").startswith("/data/")
            or Path("/system/bin").exists()
        )

    @classmethod
    def _trusted_binary_roots(cls) -> tuple[Path, ...]:
        if cls._is_android():
            prefix = Path(os.environ.get("PREFIX", ""))
            roots = (prefix / "bin", Path("/system/bin"), Path("/system/xbin"))
        else:
            roots = (Path("/usr/bin"), Path("/bin"), Path("/usr/local/bin"))
        return tuple(root.resolve() for root in roots if root.is_dir())

    def _resolve_executable(self, token: str) -> tuple[str | None, str]:
        basename = Path(token).name.lower()
        if basename in _DESTRUCTIVE_EXECUTABLES:
            return None, "DESTRUCTIVE_COMMAND_DENIED"
        if basename in _SHELLS:
            return None, "SHELL_EXECUTION_DENIED"
        if basename in _INTERPRETERS or re.fullmatch(r"python\d*(?:\.\d+)*", basename):
            return None, "INTERPRETER_EXECUTION_DENIED"
        if basename in _LAUNCHERS:
            return None, "EXECUTION_WRAPPER_DENIED"
        if basename not in _SAFE_EXECUTABLES:
            return None, "EXECUTABLE_NOT_ALLOWLISTED"

        roots = self._trusted_binary_roots()
        if "/" in token or "\\" in token:
            candidate = Path(token)
            if not candidate.is_absolute():
                return None, "RELATIVE_EXECUTABLE_PATH_DENIED"
        else:
            search_path = os.pathsep.join(str(root) for root in roots)
            located = shutil.which(token, path=search_path)
            if located is None:
                return None, "EXECUTABLE_NOT_FOUND_IN_TRUSTED_PATH"
            candidate = Path(located)

        # Check both the entry point and its resolved target. This blocks a
        # workspace symlink named "ls" that actually points to Python.
        try:
            candidate_absolute = Path(os.path.abspath(candidate))
            candidate_parent = candidate_absolute.parent.resolve(strict=True)
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            return None, "EXECUTABLE_PATH_INVALID"
        if not any(candidate_parent == root or root in candidate_parent.parents for root in roots):
            return None, "EXECUTABLE_ENTRY_OUTSIDE_TRUSTED_PATH"
        # The visible entry point's parent is already verified against the
        # trusted binary roots above. OS packages legitimately use symlinks
        # whose targets have implementation-specific names and locations.
        # Workspace-originated links remain denied because their parent is
        # outside the trusted roots.
        target_name = resolved.name.lower()
        expected_aliases = {basename, f"gnu{basename}", *_MULTICALL_BINARIES}
        if target_name not in expected_aliases:
            return None, f"EXECUTABLE_TARGET_MISMATCH:{basename}->{target_name}"
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return None, "EXECUTABLE_NOT_RUNNABLE"

        # Keep the original symlink entry point; Termux coreutils dispatches by
        # the invoked name (for example, .../bin/ls -> coreutils).
        return str(candidate), "GUARD_ALLOWED"

    def check(self, *, tool: str, arguments: dict) -> GuardResult:
        if tool == "jalankan_python":
            if not isinstance(arguments.get("nama"), str) or not arguments["nama"].strip():
                return GuardResult(False, "PYTHON_SCRIPT_REQUIRED")
            if not self.workspace_root.is_dir():
                return GuardResult(False, "WORKSPACE_NOT_CONFIGURED")
            if self.workspace_root == Path(self.workspace_root.anchor):
                return GuardResult(False, "WORKSPACE_ROOT_TOO_BROAD")
            available, reason = namespace_sandbox_status()
            if not available:
                return GuardResult(False, f"PYTHON_SANDBOX_UNAVAILABLE:{reason}")
            for helper in ("setpriv", "env"):
                if find_trusted_system_executable(helper) is None:
                    return GuardResult(
                        False,
                        f"PYTHON_SANDBOX_UNAVAILABLE: trusted {helper} utility is required",
                    )
            return GuardResult(True, "PYTHON_SANDBOX_AVAILABLE")
        if tool != "run_command":
            return GuardResult(True, "GUARD_NOT_APPLICABLE")

        cwd = arguments.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip():
            return GuardResult(False, "WORKSPACE_CWD_REQUIRED")
        try:
            resolved_cwd = Path(cwd).resolve(strict=True)
        except (OSError, RuntimeError):
            return GuardResult(False, "WORKSPACE_CWD_INVALID")
        if not resolved_cwd.is_dir() or not self._inside_workspace(resolved_cwd):
            return GuardResult(False, "WORKSPACE_BOUNDARY_VIOLATION")

        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return GuardResult(False, "COMMAND_REQUIRED")
        try:
            argv = shlex.split(command)
        except (TypeError, ValueError):
            return GuardResult(False, "COMMAND_PARSE_ERROR")
        if not argv:
            return GuardResult(False, "COMMAND_EMPTY")

        executable, reason = self._resolve_executable(argv[0])
        if executable is None:
            return GuardResult(False, reason)

        name = Path(argv[0]).name.lower()
        args = argv[1:]
        if name == "sleep":
            if (
                len(args) != 1
                or not re.fullmatch(r"(?:\d+(?:\.\d*)?|\.\d+)", args[0])
                or not 0 < float(args[0]) <= 60
            ):
                return GuardResult(False, "SLEEP_ARGUMENT_INVALID")
        if name in {"pwd", "whoami", "true", "false"} and args:
            return GuardResult(False, "COMMAND_ARGUMENTS_NOT_ALLOWED")
        if name == "ls":
            for item in args:
                if item.startswith("-"):
                    if item not in _LS_FLAGS:
                        return GuardResult(False, "LS_OPTION_NOT_ALLOWLISTED")
                    continue
                target = Path(item)
                if not target.is_absolute():
                    target = resolved_cwd / target
                try:
                    resolved_target = target.resolve(strict=True)
                except (OSError, RuntimeError):
                    return GuardResult(False, "LS_PATH_INVALID")
                if not self._inside_workspace(resolved_target):
                    return GuardResult(False, "WORKSPACE_BOUNDARY_VIOLATION")
        if name in {"echo", "printf"} and any(
            token in {";", "&&", "||", "|", ">", "<", "`"}
            for token in args
        ):
            return GuardResult(False, "COMMAND_CONTROL_TOKEN_DENIED")

        return GuardResult(True, "GUARD_ALLOWED")

    def _inside_workspace(self, path: Path) -> bool:
        try:
            path.relative_to(self.workspace_root)
            return True
        except ValueError:
            return False
