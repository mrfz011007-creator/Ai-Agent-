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
ANDROID_MAX_WALL_SECONDS = 120.0
ANDROID_MAX_CPU_SECONDS = 30
ANDROID_MAX_FILE_MB = 16
ANDROID_MAX_OUTPUT_BYTES = 64 * 1024


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
                    break
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
    is_android = (
        os.environ.get("PREFIX", "").startswith("/data/")
        or Path("/system/bin").exists()
    )
    if is_android:
        cpu_seconds = min(cpu_seconds, ANDROID_MAX_CPU_SECONDS)
    memory_mb = None
    if not is_android:
        memory_mb = _read_limit_env(
            "AI_AGENT_PROCESS_MEMORY_MB", DEFAULT_MEMORY_LIMIT_MB, MIN_MEMORY_LIMIT_MB
        )
    file_mb = _read_limit_env(
        "AI_AGENT_PROCESS_FILE_MB", DEFAULT_FILE_LIMIT_MB, MIN_FILE_LIMIT_MB
    )
    if is_android:
        file_mb = min(file_mb, ANDROID_MAX_FILE_MB)

    def set_capped(limit_name: str, desired: int) -> None:
        if not hasattr(resource, limit_name):
            return
        limit = getattr(resource, limit_name)
        _current_soft, current_hard = resource.getrlimit(limit)
        if current_hard != resource.RLIM_INFINITY:
            desired = min(desired, current_hard)
        resource.setrlimit(limit, (desired, desired))

    set_capped("RLIMIT_CPU", cpu_seconds)
    set_capped("RLIMIT_NOFILE", 64 if is_android else 128)
    set_capped("RLIMIT_NPROC", 32 if is_android else 128)
    set_capped("RLIMIT_CORE", 0)
    set_capped("RLIMIT_FSIZE", file_mb * 1024 * 1024)

    # Android/Bionic can fail during process startup when RLIMIT_AS is applied.
    # Termux still receives CPU, file, descriptor, process, output and wall limits.
    if hasattr(resource, "RLIMIT_AS") and memory_mb is not None:
        set_capped("RLIMIT_AS", memory_mb * 1024 * 1024)



def python_resource_limits_available() -> bool:
    required = (
        "RLIMIT_CPU", "RLIMIT_NOFILE", "RLIMIT_NPROC",
        "RLIMIT_CORE", "RLIMIT_FSIZE", "RLIMIT_AS",
    )
    return resource is not None and all(hasattr(resource, name) for name in required)


def _apply_python_resource_limits(timeout: float) -> None:
    """Apply fixed resource caps to the isolated Python process tree."""
    if not python_resource_limits_available():
        raise OSError("Required Python runner resource limits are unavailable")

    def set_limit(name: str, desired: int) -> None:
        limit = getattr(resource, name)
        _soft, hard = resource.getrlimit(limit)
        if hard != resource.RLIM_INFINITY:
            desired = min(desired, hard)
        if desired < 0:
            raise OSError(f"Unable to apply required resource limit: {name}")
        resource.setrlimit(limit, (desired, desired))

    set_limit("RLIMIT_CPU", max(1, min(30, math.ceil(timeout))))
    set_limit("RLIMIT_NOFILE", 64)
    set_limit("RLIMIT_NPROC", 32)
    set_limit("RLIMIT_CORE", 0)
    set_limit("RLIMIT_FSIZE", 16 * 1024 * 1024)
    set_limit("RLIMIT_AS", 512 * 1024 * 1024)


def run_command(
    *,
    command: str,
    cwd: str,
    timeout: float = 900.0,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    max_output_chars: int | None = None,
) -> dict:
    """Public process entry point; every caller must pass the command guard."""
    if not isinstance(command, str) or len(command) > 4096:
        return {
            "success": False,
            "status": "COMMAND_INVALID",
            "exit_code": None,
            "stdout": "",
            "stderr": "command must be a string no longer than 4096 characters",
            "sandbox_mode": "not_started",
        }
    if not isinstance(cwd, str) or not cwd.strip():
        return {
            "success": False,
            "status": "WORKSPACE_CWD_REQUIRED",
            "exit_code": None,
            "stdout": "",
            "stderr": "cwd must be a non-empty workspace path",
            "sandbox_mode": "not_started",
        }

    try:
        from security.guard import GuardEngine

        workspace = os.environ.get("AI_AGENT_WORKSPACE_ROOT")
        if not workspace:
            return {
                "success": False,
                "status": "WORKSPACE_NOT_CONFIGURED",
                "exit_code": None,
                "stdout": "",
                "stderr": "AI_AGENT_WORKSPACE_ROOT must be configured; caller cwd is not trusted",
                "sandbox_mode": "not_started",
            }
        decision = GuardEngine(workspace).check(
            tool="run_command",
            arguments={"command": command, "cwd": cwd},
        )
    except Exception as error:
        return {
            "success": False,
            "status": "GUARD_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
            "sandbox_mode": "not_started",
        }

    if not decision.allowed:
        return {
            "success": False,
            "status": "GUARD_DENIED",
            "exit_code": None,
            "stdout": "",
            "stderr": decision.reason,
            "sandbox_mode": "not_started",
        }

    return _run_command_unchecked(
        command=command,
        cwd=cwd,
        timeout=timeout,
        output_limit=output_limit,
        max_output_chars=max_output_chars,
    )


def _run_command_unchecked(
    *,
    command: str,
    cwd: str,
    timeout: float = 900.0,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
    max_output_chars: int | None = None,
    stdin_data: bytes | None = None,
    require_namespace: bool = False,
    extra_readonly_roots: tuple[str, ...] = (),
    resource_profile: str = "default",
) -> dict:
    """Internal executor. Call only after a GuardEngine decision."""

    """Execute without a shell and enforce wall, output, and OS resource limits."""
    if max_output_chars is not None:
        output_limit = max_output_chars
    if stdin_data is not None and not isinstance(stdin_data, bytes):
        return {"success": False, "status": "INVALID_STDIN_DATA", "exit_code": None,
                "stdout": "", "stderr": "stdin_data must be bytes", "sandbox_mode": "not_started"}
    if resource_profile not in {"default", "python"}:
        return {"success": False, "status": "INVALID_RESOURCE_PROFILE", "exit_code": None,
                "stdout": "", "stderr": "Unknown resource profile", "sandbox_mode": "not_started"}
    if resource_profile == "python" and not python_resource_limits_available():
        return {"success": False, "status": "SANDBOX_UNAVAILABLE", "exit_code": None,
                "stdout": "", "stderr": "Required Python runner resource limits are unavailable",
                "sandbox_mode": "unavailable"}
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

    is_android = (
        os.environ.get("PREFIX", "").startswith("/data/")
        or Path("/system/bin").exists()
    )
    if is_android:
        # Conservative hard caps for shared Termux devices. These limits are
        # applied even when callers request a larger timeout or output buffer.
        timeout = min(float(timeout), ANDROID_MAX_WALL_SECONDS)
        output_limit = min(output_limit, ANDROID_MAX_OUTPUT_BYTES)

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
    output_limit_killed = False

    try:
        workspace = str(Path(cwd).resolve())
        try:
            sandbox_command, sandbox_env, sandbox_root = prepare_sandbox(
                argv, cwd=workspace, extra_readonly_roots=extra_readonly_roots
            )
            sandbox_mode = "namespace"
        except SandboxUnavailable as error:
            if require_namespace:
                return {
                    "success": False, "status": "SANDBOX_UNAVAILABLE", "exit_code": None,
                    "stdout": "", "stderr": redact_text(str(error)), "sandbox_mode": "unavailable",
                }
            sandbox_command, sandbox_env = prepare_hardened(argv, cwd=workspace)
            sandbox_root = None
            sandbox_mode = "hardened"

        process = subprocess.Popen(
            sandbox_command,
            cwd=workspace,
            env=sandbox_env,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=(os.name == "posix"),
            preexec_fn=(
                (
                    (lambda: _apply_python_resource_limits(float(timeout)))
                    if resource_profile == "python"
                    else (lambda: _apply_resource_limits(float(timeout)))
                )
                if os.name == "posix"
                else None
            ),
        )

        if stdin_data is not None and process.stdin is not None:
            try:
                process.stdin.write(stdin_data)
                process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                process.stdin.close()

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
                # Let a short command finish if it already wrote a bounded
                # one-shot result. A continuing writer is stopped promptly.
                try:
                    process.wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    output_limit_killed = True
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
        if limited and (output_limit_killed or process.returncode != 0):
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
            "output_truncated": limited,
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
    except ValueError as error:
        return {
            "success": False,
            "status": "INVALID_RESOURCE_LIMIT",
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
