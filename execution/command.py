from __future__ import annotations

import os
import shlex
import signal
import subprocess
import tempfile
from pathlib import Path

try:
    import resource
except ImportError:  # pragma: no cover - non-POSIX fallback
    resource = None

from execution.sandbox import (SandboxUnavailable, SandboxPolicyError, cleanup_sandbox, prepare_hardened, prepare_sandbox)


DEFAULT_OUTPUT_LIMIT = 100_000
DEFAULT_MEMORY_LIMIT_MB = 1024
MIN_MEMORY_LIMIT_MB = 128


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
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
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

    stdout_file = tempfile.NamedTemporaryFile(prefix="ai-agent-out-", delete=False)
    stderr_file = tempfile.NamedTemporaryFile(prefix="ai-agent-err-", delete=False)
    stdout_path, stderr_path = stdout_file.name, stderr_file.name
    stdout_file.close()
    stderr_file.close()

    sandbox_root = None
    stdout_handle = open(stdout_path, "wb")
    stderr_handle = open(stderr_path, "wb")
    try:
        try:
            sandbox_command, sandbox_env, sandbox_root = prepare_sandbox(
                argv,
                cwd=str(Path(cwd).resolve()),
            )
            sandbox_mode = "namespace"
        except SandboxUnavailable:
            sandbox_command, sandbox_env = prepare_hardened(
                argv,
                cwd=str(Path(cwd).resolve()),
            )
            sandbox_root = None
            sandbox_mode = "hardened"

        def _limits() -> None:
            if resource is None:
                return
            cpu_seconds = max(1, int(timeout) + 1)
            memory_mb = int(os.environ.get("AI_AGENT_PROCESS_MEMORY_MB", str(DEFAULT_MEMORY_LIMIT_MB)))
            if memory_mb < MIN_MEMORY_LIMIT_MB:
                raise ValueError(
                    f"AI_AGENT_PROCESS_MEMORY_MB must be >= {MIN_MEMORY_LIMIT_MB}"
                )
            memory_bytes = memory_mb * 1024 * 1024
            if hasattr(resource, "RLIMIT_CPU"):
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            if hasattr(resource, "RLIMIT_NOFILE"):
                resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
            if hasattr(resource, "RLIMIT_NPROC"):
                resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
            # Android/Bionic may fail during process startup when RLIMIT_AS
            # is applied. Keep this limit on other POSIX systems, but skip it
            # on Android/Termux. CPU, NOFILE, NPROC and wall-clock limits remain.
            is_android = (
                os.environ.get("PREFIX", "").startswith("/data/")
                or Path("/system/bin").exists()
            )
            if hasattr(resource, "RLIMIT_AS") and not is_android:
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))

        process = subprocess.Popen(
            sandbox_command,
            cwd=str(Path(cwd).resolve()),
            env=sandbox_env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=(os.name == "posix"),
            preexec_fn=_limits if os.name == "posix" else None,
        )
        stdout_handle.close()
        stderr_handle.close()
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
            "status": ("SUCCESS" if process.returncode == 0 else "FAILED"),
            "exit_code": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "output_truncated": truncated,
            "sandbox_mode": sandbox_mode,
        }
    except ValueError as error:
        return {
            "success": False,
            "status": "INVALID_RESOURCE_LIMIT",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }
    except SandboxPolicyError as error:
        return {
            "success": False,
            "status": "SANDBOX_POLICY_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }
    except subprocess.SubprocessError as error:
        try:
            stdout_handle.close()
        except Exception:
            pass
        try:
            stderr_handle.close()
        except Exception:
            pass
        return {
            "success": False,
            "status": "EXECUTION_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }
    except OSError as error:
        try:
            stdout_handle.close()
        except Exception:
            pass
        try:
            stderr_handle.close()
        except Exception:
            pass
        return {
            "success": False,
            "status": "EXECUTION_ERROR",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
        }
    finally:
        if sandbox_root:
            cleanup_sandbox(sandbox_root)
        for path in (stdout_path, stderr_path):
            try:
                Path(path).unlink()
            except FileNotFoundError:
                pass
