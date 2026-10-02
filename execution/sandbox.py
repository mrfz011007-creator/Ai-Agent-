from __future__ import annotations

import os
import shlex
import shutil
import tempfile
import uuid
from pathlib import Path


class SandboxUnavailable(RuntimeError):
    """Raised when the OS cannot provide the required process boundary."""


class SandboxPolicyError(ValueError):
    """Raised when a command cannot be represented safely in the sandbox."""


_DENIED_ENV_TOKENS = (
    "API_KEY",
    "TOKEN",
    "PASSWORD",
    "SECRET",
    "PRIVATE_KEY",
    "CREDENTIAL",
)


def _safe_env(source: dict[str, str], workspace: Path) -> dict[str, str]:
    keep = {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "JAVA_HOME",
        "ANDROID_HOME",
        "ANDROID_SDK_ROOT",
        "GRADLE_USER_HOME",
        "TMPDIR",
        "PREFIX",
    }
    result: dict[str, str] = {}
    for key, value in source.items():
        upper = key.upper()
        if any(token in upper for token in _DENIED_ENV_TOKENS):
            continue
        if key in keep:
            result[key] = value

    result["HOME"] = str(workspace / ".sandbox-home")
    result["TMPDIR"] = str(workspace / ".sandbox-tmp")
    result["GRADLE_USER_HOME"] = str(workspace / ".gradle")
    return result


def _resolve_executable(argv: list[str]) -> str:
    executable = argv[0]
    if executable.startswith("./") or executable.startswith("../") or "/" in executable:
        resolved = Path(executable).resolve()
        if not resolved.exists():
            raise SandboxPolicyError(f"Executable not found: {executable}")
        return str(resolved)
    resolved = shutil.which(executable)
    if resolved is None:
        raise SandboxPolicyError(f"Executable not found: {executable}")

    # Termux exposes several coreutils commands (for example pwd and ls)
    # through symlinks to the multi-call `coreutils` binary. Resolving those
    # symlinks changes `pwd` into `coreutils` without its required applet
    # name and therefore breaks execution. Preserve the original executable
    # path on Android/Termux; keep canonical resolution on regular POSIX.
    is_android = (
        os.environ.get("PREFIX", "").startswith("/data/")
        or Path("/system/bin").exists()
    )
    if is_android:
        return resolved

    return str(Path(resolved).resolve())


def _bind_sources(executable: str, env: dict[str, str]) -> list[Path]:
    sources: list[Path] = []
    for candidate in (Path("/usr"), Path("/etc"), Path("/dev")):
        if candidate.exists():
            sources.append(candidate)

    executable_path = Path(executable)
    for key in ("PREFIX", "ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME"):
        value = env.get(key)
        if value:
            path = Path(value).resolve()
            if path.exists() and path not in sources:
                sources.append(path)

    parent = executable_path.parent
    if parent.exists() and parent not in sources:
        sources.append(parent)

    # Keep common interpreter/toolchain roots available without exposing HOME.
    for part in executable_path.parents:
        if str(part) in {"/", "/usr", "/etc", "/dev"}:
            break
        if part == Path("/data/data/com.termux/files/usr"):
            if part.exists() and part not in sources:
                sources.append(part)
            break
    return sources


def prepare_hardened(
    argv: list[str],
    *,
    cwd: str,
    source_env: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str]]:
    """Portable fallback when kernel namespaces are unavailable.

    It never invokes a shell, removes credential-like environment variables,
    confines the working directory to the selected workspace, and relies on
    the caller's resource limits. Network isolation is unavailable in this mode.
    """
    workspace = Path(cwd).resolve()
    if not workspace.is_dir():
        raise SandboxPolicyError("Sandbox workspace must be an existing directory")
    if any(token in argv[0] for token in (";", "|", "&", ">", "<", "$", "`")):
        raise SandboxPolicyError("Shell control syntax is not allowed")
    executable = _resolve_executable(argv)
    env = _safe_env(dict(source_env or os.environ), workspace)
    return [executable, *argv[1:]], env


def prepare_sandbox(
    argv: list[str],
    *,
    cwd: str,
    source_env: dict[str, str] | None = None,
) -> tuple[list[str], dict[str, str], str]:
    """Build a Linux user/mount/network namespace command.

    The sandbox exposes system/toolchain roots read-only, the workspace read-write,
    a private /tmp, and an isolated network namespace. It deliberately does not
    expose the caller's HOME or arbitrary writable host paths.
    """
    if os.name != "posix" or not Path("/proc").exists():
        raise SandboxUnavailable("OS sandbox requires a POSIX /proc environment")

    # Android/Termux does not provide the same FHS layout assumed by the
    # namespace/chroot launcher below. Use the portable hardened fallback
    # instead of attempting a sandbox command that may fail after launch.
    prefix = os.environ.get("PREFIX", "")
    if prefix.startswith("/data/") or Path("/system/bin").exists():
        raise SandboxUnavailable("Android/Termux namespace layout is unsupported")

    unshare = shutil.which("unshare")
    chroot = shutil.which("chroot")
    if not unshare or not chroot:
        raise SandboxUnavailable("unshare/chroot are required for namespace isolation")

    try:
        probe = __import__("subprocess").run(
            [unshare, "--user", "--map-root-user", "--mount", "--net", "true"],
            stdout=__import__("subprocess").DEVNULL,
            stderr=__import__("subprocess").DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, __import__("subprocess").SubprocessError) as error:
        raise SandboxUnavailable("Linux namespace probe failed") from error
    if probe.returncode != 0:
        raise SandboxUnavailable("Linux user/mount/network namespaces are unavailable")

    workspace = Path(cwd).resolve()
    if not workspace.is_dir():
        raise SandboxPolicyError("Sandbox workspace must be an existing directory")

    executable = _resolve_executable(argv)
    env = _safe_env(dict(source_env or os.environ), workspace)
    env["PATH"] = ":".join(
        p for p in (
            env.get("PATH", ""),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ) if p
    )

    token = uuid.uuid4().hex
    temp_dir = Path(tempfile.gettempdir())
    root = Path(tempfile.mkdtemp(prefix=f".ai-agent-root-{token}-", dir=temp_dir))
    work_mount = workspace

    # The launcher is a shell script executed inside the new user/mount/network
    # namespace. It is removed by the parent after the child exits.
    launcher = Path(tempfile.gettempdir()) / f".ai-agent-launcher-{token}.sh"
    lines = [
        "#!/bin/sh",
        "set -eu",
        f'ROOT="{root}"',
        f'WORK="{work_mount}"',
        'mount -t tmpfs tmpfs "$ROOT"',
        'mkdir -p "$ROOT/usr" "$ROOT/etc" "$ROOT/dev" "$ROOT/proc" "$ROOT/tmp"',
        'ln -s usr/bin "$ROOT/bin"',
        'ln -s usr/lib "$ROOT/lib"',
        'if [ -d "$ROOT/usr/lib64" ]; then ln -s usr/lib64 "$ROOT/lib64"; fi',
    ]
    for source in _bind_sources(executable, env):
        target = root / source.relative_to("/")
        lines += [
            f'mkdir -p "{target}"',
            f'mount --bind "{source}" "{target}"',
            f'mount -o remount,ro,bind "{target}"',
        ]
    lines += [
        'mount -t proc proc "$ROOT/proc"',
        'mount -t tmpfs tmpfs "$ROOT/tmp"',
        f'mkdir -p "$ROOT{work_mount}"',
        f'mount --bind "{work_mount}" "$ROOT{work_mount}"',
        f'mount -o remount,rw,bind "$ROOT{work_mount}"',
        f'mkdir -p "$ROOT{work_mount}/.sandbox-home" "$ROOT{work_mount}/.sandbox-tmp" "$ROOT{work_mount}/.gradle"',
        f'cd "$ROOT{work_mount}"',
        f'exec chroot "$ROOT" {shlex.join([executable, *argv[1:]])}',
    ]
    # launcher is created in the host root only to pass source paths; it is not
    # mounted into the child root.
    launcher.write_text("\n".join(lines) + "\n", encoding="utf-8")
    launcher.chmod(0o700)

    # The launcher itself is the only host-side executable. User+mount+network
    # namespaces prevent privilege escalation, host writes and network access.
    shell = shutil.which("sh")
    if shell is None:
        raise SandboxUnavailable("POSIX shell is required for namespace isolation")

    command = [
        unshare,
        "--user",
        "--map-root-user",
        "--mount",
        "--net",
        "--pid",
        "--fork",
        shell,
        str(launcher),
    ]
    env["AI_AGENT_SANDBOX"] = "1"
    env["AI_AGENT_SANDBOX_ROOT"] = str(root)
    return command, env, str(root)


def cleanup_sandbox(root: str) -> None:
    shutil.rmtree(root, ignore_errors=True)
