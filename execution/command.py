from __future__ import annotations

import math
import os
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path


DEFAULT_OUTPUT_LIMIT = 100_000
MAX_COMMAND_TIMEOUT = 900.0
MAX_OUTPUT_LIMIT = DEFAULT_OUTPUT_LIMIT


def _read_limited(path: str, limit: int) -> tuple[str, bool]:
    with open(path, "rb") as stream:
        data = stream.read(limit + 1)
    truncated = len(data) > limit
    if truncated:
        data = data[:limit]
    return data.decode("utf-8", errors="replace"), truncated


def _terminate_process(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return

    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()



from security.redaction import redact_text
def run_command(
    *,
    command: str,
    cwd: str,
    timeout: float = 900.0,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    max_output_chars: int | None = None,
) -> dict:
    """Execute one bounded process; authorization is owned by ToolRouter."""
    if max_output_chars is not None:
        output_limit = max_output_chars
    if not isinstance(output_limit, int) or isinstance(output_limit, bool):
        return {
            "success": False,
            "status": "INVALID_OUTPUT_LIMIT",
            "exit_code": None,
            "stdout": "",
            "stderr": "output_limit must be an integer",
        }
    if output_limit <= 0 or output_limit > MAX_OUTPUT_LIMIT:
        return {
            "success": False,
            "status": "INVALID_OUTPUT_LIMIT",
            "exit_code": None,
            "stdout": "",
            "stderr": f"output_limit must be between 1 and {MAX_OUTPUT_LIMIT}",
        }
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        return {
            "success": False,
            "status": "INVALID_TIMEOUT",
            "exit_code": None,
            "stdout": "",
            "stderr": "timeout must be a finite positive number",
        }
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_COMMAND_TIMEOUT:
        return {
            "success": False,
            "status": "INVALID_TIMEOUT",
            "exit_code": None,
            "stdout": "",
            "stderr": f"timeout must be between 0 and {MAX_COMMAND_TIMEOUT} seconds",
        }

    try:
        argv = shlex.split(command)
    except ValueError as error:
        return {
            "success": False,
            "status": "COMMAND_PARSE_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }

    if not argv:
        return {
            "success": False,
            "status": "COMMAND_EMPTY",
            "exit_code": None,
            "stdout": "",
            "stderr": "",
        }

    stdout_file = tempfile.NamedTemporaryFile(prefix="ai-agent-out-", delete=False)
    stderr_file = tempfile.NamedTemporaryFile(prefix="ai-agent-err-", delete=False)
    stdout_path, stderr_path = stdout_file.name, stderr_file.name
    stdout_file.close()
    stderr_file.close()

    try:
        process = subprocess.Popen(
            argv,
            cwd=str(Path(cwd).resolve()),
            stdout=open(stdout_path, "wb"),
            stderr=open(stderr_path, "wb"),
            start_new_session=(os.name == "posix"),
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate_process(process)
            stdout, out_truncated = _read_limited(stdout_path, output_limit)
            stderr, err_truncated = _read_limited(stderr_path, output_limit)
            stdout = redact_text(stdout)
            stderr = redact_text(stderr)
            if out_truncated or err_truncated:
                stderr = f"{stderr}\nOUTPUT_TRUNCATED".strip()
            return {
                "success": False,
                "status": "TIMEOUT",
                "exit_code": None,
                "stdout": stdout,
                "stderr": stderr or "TIMEOUT",
            }

        stdout, out_truncated = _read_limited(stdout_path, output_limit)
        stderr, err_truncated = _read_limited(stderr_path, output_limit)
        stdout = redact_text(stdout)
        stderr = redact_text(stderr)
        truncated = out_truncated or err_truncated
        return {
            "success": process.returncode == 0,
            "status": "SUCCESS" if process.returncode == 0 else "FAILED",
            "exit_code": process.returncode,
            "stdout": stdout,