from __future__ import annotations

import math
import os
import shlex
import signal
import subprocess
import threading
import time
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX fallback
    resource = None

from execution.sandbox import (
    SandboxPolicyError,
    SandboxUnavailable,
    cleanup_sandbox,
    prepare_hardened,
    prepare_sandbox,
)
from security.redaction import redact_text


DEFAULT_OUTPUT_LIMIT = 100_000
DEFAULT_MEMORY_LIMIT_MB = 1024
MIN_MEMORY_LIMIT_MB = 128
DEFAULT_FILE_LIMIT_MB = 128
MIN_FILE_LIMIT_MB = 1


def _terminate_process(process: subprocess.Popen) -> None:
    """Terminate the process group, including children that outlive the parent."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        # The group leader can exit while a descendant is still alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
        return

    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)


def _read_pipe_bounded(
    pipe,
    output: bytearray,
    *,
    state: list[int],
    lock: threading.Lock,
    limit_event: threading.Event,
    limit: int,
) -> None:
    """Drain a child pipe while retaining no more than the shared output limit."""
    try:
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                break
            with lock:
                remaining = max(0, limit - state[0])
                if remaining:
                    retained = chunk[:remaining]
                    output.extend(retained)
                    state[0] += len(retained)
                if len(chunk) > remaining:
                    limit_event.set()
    except (OSError, ValueError):
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _read_limit_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _apply_resource_limits(timeout: float) -> None:
    if resource is None:
        return

    cpu_seconds = max(1, math.ceil(timeout))
    memory_mb = _read_limit_env(
        "AI_AGENT_PROCESS_MEMORY_MB", DEFAULT_MEMORY_LIMIT_MB, MIN_MEMORY_LIMIT_MB
    )
    file_mb = _read_limit_env(
        "AI_AGENT_PROCESS_FILE_MB", DEFAULT_FILE_LIMIT_MB, MIN_FILE_LIMIT_MB
    )

    def set_capped(limit_name: str, desired: int) -> None:
        if not hasattr(resource, limit_name):
            return
        limit = getattr(resource, limit_name)
        _current_soft, current_hard = resource.getrlimit(limit)
        if current_hard != resource.RLIM_INFINITY:
            desired = min(desired, current_hard)
        resource.setrlimit(limit, (desired, desired))

    set_capped("RLIMIT_CPU", cpu_seconds)
    set_capped("RLIMIT_NOFILE", 128)
    set_capped("RLIMIT_NPROC", 128)
    set_capped("RLIMIT_CORE", 0)
    set_capped("RLIMIT_FSIZE", file_mb * 1024 * 1024)

    # Android/Bionic can fail during process startup when RLIMIT_AS is applied.
    # Termux still receives CPU, file, descriptor, process, output and wall limits.
    is_android = (
        os.environ.get("PREFIX", "").startswith("/data/")
        or Path("/system/bin").exists()
    )
    if hasattr(resource, "RLIMIT_AS") and not is_android:
        set_capped("RLIMIT_AS", memory_mb * 1024 * 1024)


def run_command(
    *,
    command: str,
    cwd: str,
    timeout: float = 900.0,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    max_output_chars: int | None = None,
) -> dict:
    """Execute without a shell and enforce wall, output, and OS resource limits."""
    if max_output_chars is not None:
        output_limit = max_output_chars
    if isinstance(output_limit, bool) or not isinstance(output_limit, int) or output_limit <= 0:
        return {
            "success": False,
            "status": "INVALID_OUTPUT_LIMIT",
            "exit_code": None,
            "stdout": "",
            "stderr": "output_limit must be a positive integer",
        }
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        return {
            "success": False,
            "status": "INVALID_TIMEOUT",
            "exit_code": None,
            "stdout": "",
            "stderr": "timeout must be a finite positive number",
        }

    try:
        argv = shlex.split(command)
    except (TypeError, ValueError) as error:
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

    sandbox_root = None
    sandbox_mode = "unknown"
    process = None
    readers: list[threading.Thread] = []
    stdout_data = bytearray()
    stderr_data = bytearray()
    state = [0]
    output_lock = threading.Lock()
    output_limit_hit = threading.Event()
    timed_out = False

    try:
        workspace = str(Path(cwd).resolve())
        try:
            sandbox_command, sandbox_env, sandbox_root = prepare_sandbox(
                argv, cwd=workspace
            )
            sandbox_mode = "namespace"
        except SandboxUnavailable:
            sandbox_command, sandbox_env = prepare_hardened(argv, cwd=workspace)
            sandbox_root = None
            sandbox_mode = "hardened"

        process = subprocess.Popen(
            sandbox_command,
            cwd=workspace,
            env=sandbox_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=(os.name == "posix"),
            preexec_fn=(
                (lambda: _apply_resource_limits(float(timeout)))
                if os.name == "posix"
                else None
            ),
        )

        for pipe, destination, name in (
            (process.stdout, stdout_data, "stdout"),
            (process.stderr, stderr_data, "stderr"),
        ):
            reader = threading.Thread(
                target=_read_pipe_bounded,
                args=(pipe, destination),
                kwargs={
                    "state": state,
                    "lock": output_lock,
                    "limit_event": output_limit_hit,
                    "limit": output_limit,
                },
                name=f"ai-agent-{name}-reader",
                daemon=True,
            )
            reader.start()
            readers.append(reader)

        deadline = time.monotonic() + float(timeout)
        while process.poll() is None:
            if output_limit_hit.is_set():
                _terminate_process(process)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _terminate_process(process)
                break
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue

        # A normal command must not leave children holding inherited pipe handles.
        for reader in readers:
            reader.join(timeout=0.5)
        if any(reader.is_alive() for reader in readers):
            _terminate_process(process)
            for reader in readers:
                reader.join(timeout=0.5)

        stdout = redact_text(stdout_data.decode("utf-8", errors="replace"))
        stderr = redact_text(stderr_data.decode("utf-8", errors="replace"))
        limited = output_limit_hit.is_set()

        if timed_out:
            return {
                "success": False,
                "status": "TIMEOUT",
                "exit_code": None,
                "stdout": stdout,
                "stderr": stderr or "TIMEOUT",
                "output_truncated": limited,
                "sandbox_mode": sandbox_mode,
            }
        if limited:
            return {
                "success": False,
                "status": "OUTPUT_LIMIT_EXCEEDED",
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "output_truncated": True,
                "sandbox_mode": sandbox_mode,
            }

        return {
            "success": process.returncode == 0,
            "status": "SUCCESS" if process.returncode == 0 else "FAILED",
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": False,
            "sandbox_mode": sandbox_mode,
        }
    except ValueError as error:
        return {
            "success": False,
            "status": "INVALID_RESOURCE_LIMIT",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
            "sandbox_mode": sandbox_mode,
        }
    except SandboxPolicyError as error:
        return {
            "success": False,
            "status": "SANDBOX_POLICY_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
            "sandbox_mode": sandbox_mode,
        }
    except (OSError, subprocess.SubprocessError) as error:
        return {
            "success": False,
            "status": "EXECUTION_ERROR",
            "exit_code": None,
            "stdout": redact_text(stdout_data.decode("utf-8", errors="replace")),
            "stderr": redact_text(str(error)),
            "sandbox_mode": sandbox_mode,
        }
    finally:
        if process is not None and process.poll() is None:
            _terminate_process(process)
        for reader in readers:
            reader.join(timeout=0.2)
        if sandbox_root:
            cleanup_sandbox(sandbox_root)
