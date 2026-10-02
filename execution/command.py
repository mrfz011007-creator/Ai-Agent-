from __future__ import annotations

import os
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path


DEFAULT_OUTPUT_LIMIT = 100_000


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



def _redact_output(value: str) -> str:
    import re
    value = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+", r"\1***REDACTED***", value)
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}", r"\1[REDACTED_SECRET]", value)
    value = re.sub(r"\bAIza[0-9A-Za-z_-]{20,}\b", "[REDACTED_SECRET]", value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{20,}\b", "[REDACTED_SECRET]", value)
    return value

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
    if output_limit <= 0:
        return {
            "success": False,
            "status": "INVALID_OUTPUT_LIMIT",
            "exit_code": None,
            "stdout": "",
            "stderr": "output_limit must be positive",
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
        stdout = _redact_output(stdout)
        stderr = _redact_output(stderr)
        truncated = out_truncated or err_truncated
        return {
            "success": process.returncode == 0,
            "status": "SUCCESS" if process.returncode == 0 else "FAILED",
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": truncated,
        }
    except OSError as error:
        return {
            "success": False,
            "status": "EXECUTION_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }
    finally:
        for path in (stdout_path, stderr_path):
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass
