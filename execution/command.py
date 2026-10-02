from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
from pathlib import Path


_SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)(\\s*[=:]\\s*)([^\\s,;]+)"),
    re.compile(r"(?i)(bearer\\s+)([A-Za-z0-9._~+/-]+)"),
)


def _redact(value: str) -> str:
    result = value or ""
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda m: m.group(1) + "***REDACTED***", result)
    return result


def run_command(
    *,
    command: str,
    cwd: str,
    timeout: float = 900.0,
    max_output_chars: int = 100_000,
) -> dict:
    """Execute one bounded process with process-group termination and redaction."""
    if timeout <= 0:
        return {
            "success": False,
            "status": "INVALID_TIMEOUT",
            "exit_code": None,
            "stdout": "",
            "stderr": "timeout must be positive",
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
            "stderr": "empty command",
        }

    env = os.environ.copy()
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(Path(cwd).resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=env,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = process.communicate()
            return {
                "success": False,
                "status": "TIMEOUT",
                "exit_code": process.returncode,
                "stdout": _redact(stdout)[-max_output_chars:],
                "stderr": _redact(stderr or "TIMEOUT")[-max_output_chars:],
            }
    except OSError as error:
        return {
            "success": False,
            "status": "EXECUTION_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }

    stdout = _redact(stdout)
    stderr = _redact(stderr)
    truncated = len(stdout) > max_output_chars or len(stderr) > max_output_chars
    if len(stdout) > max_output_chars:
        stdout = stdout[:max_output_chars]
    if len(stderr) > max_output_chars:
        stderr = stderr[:max_output_chars]

    return {
        "success": process.returncode == 0,
        "status": "SUCCESS" if process.returncode == 0 else "FAILED",
        "exit_code": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "output_truncated": truncated,
    }
