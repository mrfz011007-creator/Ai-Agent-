from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
from pathlib import Path


DEFAULT_OUTPUT_LIMIT = 100_000

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

    try:
        process = subprocess.Popen(
            argv,
            cwd=str(Path(cwd).resolve()),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
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
                stderr = f"{stderr}
OUTPUT_TRUNCATED".strip()
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
    except OSError as error:
        return {
            "success": False,
            "status": "EXECUTION_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }
