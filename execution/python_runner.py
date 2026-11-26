from __future__ import annotations

import math
import os
import shlex
import sys
from pathlib import Path

from execution.command import _run_command_unchecked, python_resource_limits_available
from execution.sandbox import namespace_sandbox_status
from security.workspace import (
    FileTooLargeError,
    WorkspaceSecurityError,
    max_file_bytes,
    read_file_secure,
)


MAX_PYTHON_SOURCE_BYTES = 32 * 1024
DEFAULT_PYTHON_TIMEOUT_SECONDS = 15.0
MAX_PYTHON_TIMEOUT_SECONDS = 30.0
MAX_PYTHON_OUTPUT_BYTES = 64 * 1024


def _failure(status: str, message: str) -> dict:
    return {
        "success": False,
        "status": status,
        "exit_code": None,
        "stdout": "",
        "stderr": message,
        "sandbox_mode": "not_started",
    }


def run_python_file(
    nama: str,
    timeout: float = DEFAULT_PYTHON_TIMEOUT_SECONDS,
) -> dict:
    """Run one workspace Python file only inside the strict Linux namespace sandbox."""
    if not isinstance(nama, str) or not nama.strip() or len(nama) > 1024:
        return _failure("SCRIPT_PATH_INVALID", "nama must be a relative workspace path")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or timeout <= 0
        or timeout > MAX_PYTHON_TIMEOUT_SECONDS
    ):
        return _failure(
            "INVALID_TIMEOUT",
            f"timeout must be positive and at most {MAX_PYTHON_TIMEOUT_SECONDS} seconds",
        )

    workspace_raw = os.environ.get("AI_AGENT_WORKSPACE_ROOT", "")
    if not workspace_raw.strip():
        return _failure("WORKSPACE_NOT_CONFIGURED", "AI_AGENT_WORKSPACE_ROOT is required")
    try:
        workspace = Path(workspace_raw).resolve(strict=True)
        if not workspace.is_dir():
            return _failure("WORKSPACE_INVALID", "Workspace must be an existing directory")
    except (OSError, RuntimeError):
        return _failure("WORKSPACE_INVALID", "Workspace must be an existing directory")

    available, reason = namespace_sandbox_status()
    if not available:
        return _failure("SANDBOX_UNAVAILABLE", reason)
    if not python_resource_limits_available():
        return _failure(
            "SANDBOX_UNAVAILABLE",
            "Required Linux process resource limits are unavailable",
        )

    try:
        source_limit = min(MAX_PYTHON_SOURCE_BYTES, max_file_bytes())
        source_bytes = read_file_secure(workspace, nama, source_limit)
    except FileTooLargeError:
        return _failure("CODE_TOO_LARGE", f"Python source exceeds {MAX_PYTHON_SOURCE_BYTES} bytes")
    except FileNotFoundError:
        return _failure("SCRIPT_NOT_FOUND", f"Python file not found: {nama}")
    except (WorkspaceSecurityError, OSError, ValueError) as error:
        return _failure("WORKSPACE_ACCESS_DENIED", str(error))

    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return _failure("SCRIPT_ENCODING_INVALID", "Python source must be valid UTF-8")
    if not source.strip() or "\x00" in source:
        return _failure("SCRIPT_INVALID", "Python source must be non-empty and contain no NUL bytes")

    script_path = str(workspace / nama)
    script_directory = str((workspace / nama).parent)
    bootstrap = (
        "import sys\n"
        f"sys.path.insert(0, {script_directory!r})\n"
        f"script_path = {script_path!r}\n"
        "sys.argv = [script_path]\n"
        "source = sys.stdin.buffer.read().decode('utf-8')\n"
        "namespace = {'__name__': '__main__', '__file__': script_path, '__package__': None}\n"
        "exec(compile(source, script_path, 'exec', dont_inherit=True), namespace, namespace)\n"
    )
    argv = [sys.executable, "-I", "-S", "-B", "-c", bootstrap]
    result = _run_command_unchecked(
        command=shlex.join(argv),
        cwd=str(workspace),
        timeout=float(timeout),
        output_limit=MAX_PYTHON_OUTPUT_BYTES,
        stdin_data=source_bytes,
        require_namespace=True,
        extra_readonly_roots=(sys.base_prefix,),
        resource_profile="python",
    )
    result["script"] = nama
    return result
