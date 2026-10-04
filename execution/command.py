from __future__ import annotations

import math
import os
import shlex
import signal
import subprocess
import threading
from pathlib import Path

try:
    import resource
except ImportError:
    resource = None

from security.redaction import redact_text


DEFAULT_OUTPUT_LIMIT = 100_000
DEFAULT_MEMORY_LIMIT_MB = 1024
MIN_MEMORY_LIMIT_MB = 128

def _read_pipe_limited(stream, limit: int) -> tuple[str, bool]:
    data = bytearray()
    truncated = False
    while True:
        chunk = stream.read(8192)
        if not chunk:
            break
        remaining = limit - len(data)
        if remaining > 0:
            data.extend(chunk[:remaining])
        if len(chunk) > max(0, remaining):
            truncated = True
    return bytes(data).decode("utf-8", errors="replace"), truncated


def _start_pipe_reader(stream, limit: int):
    result = {"data": "", "truncated": False}

    def reader():
        result["data"], result["truncated"] = _read_pipe_limited(stream, limit)

    worker = threading.Thread(target=reader, daemon=True)
    worker.start()
    return worker, result



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
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        return {
            "success": False,
            "status": "INVALID_TIMEOUT",
            "exit_code": None,
            "stdout": "",
            "stderr": "timeout must be a finite positive number",
        }
    if (
        isinstance(output_limit, bool)
        or not isinstance(output_limit, int)
        or output_limit <= 0
    ):
        return {
            "success": False,
            "status": "INVALID_OUTPUT_LIMIT",
            "exit_code": None,
            "stdout": "",
            "stderr": "output_limit must be a positive integer",
        }
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

    def _limits() -> None:
        if resource is None:
            return
        cpu_seconds = max(1, int(timeout) + 1)
        if hasattr(resource, "RLIMIT_CPU"):
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if hasattr(resource, "RLIMIT_NOFILE"):
            resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
        if hasattr(resource, "RLIMIT_NPROC"):
            resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))

        memory_mb = int(
            os.environ.get(
                "AI_AGENT_PROCESS_MEMORY_MB",
                str(DEFAULT_MEMORY_LIMIT_MB),
            )
        )
        if memory_mb < MIN_MEMORY_LIMIT_MB:
            raise ValueError(
                f"AI_AGENT_PROCESS_MEMORY_MB must be >= {MIN_MEMORY_LIMIT_MB}"
            )

        is_android = (
            os.environ.get("PREFIX", "").startswith("/data/")
            or Path("/system/bin").exists()
        )
        if hasattr(resource, "RLIMIT_AS") and not is_android:
            memory_bytes = memory_mb * 1024 * 1024
            resource.setrlimit(
                resource.RLIMIT_AS,
                (memory_bytes, memory_bytes),
            )

    try:
        process = subprocess.Popen(
            argv,
            cwd=str(Path(cwd).resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
            preexec_fn=_limits if os.name == "posix" else None,
        )
        stdout_thread, stdout_result = _start_pipe_reader(process.stdout, output_limit)
        stderr_thread, stderr_result = _start_pipe_reader(process.stderr, output_limit)

        timed_out = False
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process(process)
        finally:
            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)

            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

        stdout = redact_text(stdout_result["data"])
        stderr = redact_text(stderr_result["data"])
        truncated = bool(stdout_result["truncated"] or stderr_result["truncated"])

        if timed_out:
            if truncated:
                stderr = f"{stderr}\nOUTPUT_TRUNCATED".strip()
            return {
                "success": False,
                "status": "TIMEOUT",
                "exit_code": None,
                "stdout": stdout,
                "stderr": stderr or "TIMEOUT",
                "output_truncated": truncated,
            }

        return {
            "success": process.returncode == 0,
            "status": "SUCCESS" if process.returncode == 0 else "FAILED",
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": truncated,
        }
    except ValueError as error:
        return {
            "success": False,
            "status": "INVALID_RESOURCE_LIMIT",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "success": False,
            "status": "EXECUTION_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }
