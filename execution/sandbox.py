from __future__ import annotations

import os
import shlex
import shutil
import subprocess
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

    is_android = (
        source.get("PREFIX", "").startswith("/data/")
        or Path("/system/bin").exists()
    )
    if is_android:
        trusted_path = [
            Path(source.get("PREFIX", "")) / "bin",
            Path("/system/bin"),
            Path("/system/xbin"),
        ]
        result["PATH"] = os.pathsep.join(
            str(path) for path in trusted_path if path.is_dir()
        )

    result["HOME"] = str(workspace / ".sandbox-home")
    result["TMPDIR"] = str(workspace / ".sandbox-tmp")
    result["GRADLE_USER_HOME"] = str(workspace / ".gradle")
    return result


def _resolve_executable(argv: list[str]) -> str:
    executable = argv[0]
    is_android = (
        os.environ.get("PREFIX", "").startswith("/data/")
        or Path("/system/bin").exists()
    )

    if executable.startswith("./") or executable.startswith("../") or "/" in executable:
        resolved = Path(executable).resolve()
        if not resolved.exists():
            raise SandboxPolicyError(f"Executable not found: {executable}")
        # Preserve Termux symlink entry points even when the caller supplies
        # an explicit executable path (for example .../bin/pwd).
        return str(Path(executable)) if is_android else str(resolved)
    search_path = os.environ.get("PATH", "")
    if is_android:
        prefix = Path(os.environ.get("PREFIX", ""))
        trusted = [prefix / "bin", Path("/system/bin"), Path("/system/xbin")]
        search_path = os.pathsep.join(
            str(path) for path in trusted if path.is_dir()
        )
    resolved = shutil.which(executable, path=search_path)
    if resolved is None:
        raise SandboxPolicyError(f"Executable not found in trusted PATH: {executable}")

    # Termux exposes several coreutils commands (for example pwd and ls)
    # through symlinks to the multi-call `coreutils` binary. Resolving those
    # symlinks changes `pwd` into `coreutils` without its required applet
    # name and therefore breaks execution. Preserve the original executable
    # path on Android/Termux; keep canonical resolution on regular POSIX.
    if is_android:
        return resolved

    return str(Path(resolved).resolve())


def _bind_sources(
    executable: str,
    env: dict[str, str],
    extra_readonly_roots: tuple[str, ...] = (),
) -> list[Path]:
    sources: list[Path] = []

    def add_source(candidate: str | Path) -> None:
        path = Path(candidate).resolve()
        if not path.exists() or not path.is_dir():
            return
        if path == Path("/"):
            raise SandboxPolicyError("Binding the host root into the sandbox is denied")
        # If an existing read-only mount already contains this path, it
        # exposes the candidate and all of its runtime libraries. Do not replace
        # that parent with a narrower child such as /usr/local/bin.
        if any(path == source or source in path.parents for source in sources):
            return
        # A newly added parent mount subsumes any previously added children.
        sources[:] = [source for source in sources if path not in source.parents]
        sources.append(path)

    for candidate in (Path("/usr"), Path("/etc"), *extra_readonly_roots):
        add_source(candidate)
    for key in ("PREFIX", "ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME"):
        value = env.get(key)
        if value:
            add_source(value)
    add_source(Path(executable).parent)
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
    for name in (".sandbox-home", ".sandbox-tmp", ".gradle"):
        directory = workspace / name
        if directory.is_symlink():
            raise SandboxPolicyError(f"Sandbox directory must not be a symlink: {name}")
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.resolve().is_relative_to(workspace):
            raise SandboxPolicyError(f"Sandbox directory escapes workspace: {name}")

    executable = _resolve_executable(argv)
    env = _safe_env(dict(source_env or os.environ), workspace)
    return [executable, *argv[1:]], env



def _is_android_or_proot() -> bool:
    source = os.environ
    if (
        source.get("PREFIX", "").startswith("/data/")
        or Path("/system/bin").exists()
        or source.get("ANDROID_ROOT")
        or source.get("ANDROID_DATA")
        or source.get("PROOT_TMP_DIR")
        or source.get("PROOT_LOADER")
        or "proot" in source.get("LD_PRELOAD", "").lower()
    ):
        return True
    # PRoot commonly exposes a non-zero TracerPid because it emulates Linux
    # filesystem/syscall behavior through ptrace.
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("TracerPid:"):
                return int(line.partition(":")[2].strip()) > 0
    except (OSError, ValueError):
        pass
    return False


def namespace_sandbox_status() -> tuple[bool, str]:
    """Probe every namespace required by strict untrusted-code execution."""
    if os.name != "posix" or not Path("/proc").exists():
        return False, "A native POSIX /proc environment is required"
    if _is_android_or_proot():
        return False, "Android, Termux, and PRoot do not provide the required OS isolation"
    unshare = shutil.which("unshare")
    chroot = shutil.which("chroot")
    if not unshare or not chroot:
        return False, "unshare and chroot are required"
    try:
        probe = subprocess.run(
            [
                unshare, "--user", "--map-root-user", "--mount",
                "--net", "--pid", "--ipc", "--uts", "--fork", "true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False, "Linux namespace capability probe failed"
    if probe.returncode != 0:
        return False, "Linux user, mount, network, and PID namespaces are unavailable"
    return True, "Required Linux namespaces are available"


def namespace_sandbox_available() -> bool:
    return namespace_sandbox_status()[0]


def prepare_sandbox(
    argv: list[str],
    *,
    cwd: str,
    source_env: dict[str, str] | None = None,
    extra_readonly_roots: tuple[str, ...] = (),
) -> tuple[list[str], dict[str, str], str]:
    """Build a Linux user/mount/network namespace command.

    The sandbox exposes system/toolchain roots read-only, the workspace read-write,
    a private /tmp, and an isolated network namespace. It deliberately does not
    expose the caller's HOME or arbitrary writable host paths.
    """
    available, reason = namespace_sandbox_status()
    if not available:
        raise SandboxUnavailable(reason)
    unshare = shutil.which("unshare")
    chroot = shutil.which("chroot")
    if not unshare or not chroot:
        raise SandboxUnavailable("unshare/chroot are required for namespace isolation")

    workspace = Path(cwd).resolve()
    if not workspace.is_dir():
        raise SandboxPolicyError("Sandbox workspace must be an existing directory")

    executable = _resolve_executable(argv)
    env = _safe_env(dict(source_env or os.environ), workspace)
    env["HOME"] = "/tmp/home"
    env["TMPDIR"] = "/tmp"
    env["GRADLE_USER_HOME"] = "/tmp/gradle"
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
    root_q = shlex.quote(str(root))
    work_q = shlex.quote(str(work_mount))
    lines = [
        "#!/bin/sh",
        "set -eu",
        'mount --make-rprivate /',
        f"ROOT={root_q}",
        f"WORK={work_q}",
        'mount -t tmpfs -o size=128m,nr_inodes=8192 tmpfs "$ROOT"',
        'mkdir -p "$ROOT/usr" "$ROOT/etc" "$ROOT/dev" "$ROOT/proc" "$ROOT/tmp"',
        'for device in null zero random urandom; do : > "$ROOT/dev/$device"; mount --bind "/dev/$device" "$ROOT/dev/$device"; done',
        'ln -s usr/bin "$ROOT/bin"',
        'ln -s usr/lib "$ROOT/lib"',
        'if [ -d "$ROOT/usr/lib64" ]; then ln -s usr/lib64 "$ROOT/lib64"; fi',
    ]
    for source in _bind_sources(executable, env, extra_readonly_roots):
        target = root / source.relative_to("/")
        source_q = shlex.quote(str(source))
        target_q = shlex.quote(str(target))
        lines += [
            f"mkdir -p {target_q}",
            f"mount --bind {source_q} {target_q}",
            f"mount -o remount,ro,bind {target_q}",
        ]
    lines += [
        'mount -t proc proc "$ROOT/proc"',
        'mount -t tmpfs -o size=16m,nr_inodes=2048 tmpfs "$ROOT/tmp"',
        'mkdir -p "$ROOT/tmp/home" "$ROOT/tmp/gradle"',
        'mkdir -p "$ROOT$WORK"',
        'mount --bind "$WORK" "$ROOT$WORK"',
        'mount -o remount,rw,bind "$ROOT$WORK"',
        'cd "$ROOT$WORK"',
        f'exec chroot "$ROOT" {shlex.join([executable, *argv[1:]])}',
    ]
    # launcher is created in the host root only to pass source paths; it is not
    # mounted into the child root.
    try:
        launcher.write_text("\n".join(lines) + "\n", encoding="utf-8")
        launcher.chmod(0o700)
    except Exception:
        cleanup_sandbox(str(root))
        raise

    # The launcher itself is the only host-side executable. User+mount+network
    # namespaces prevent privilege escalation, host writes and network access.
    shell = shutil.which("sh")
    if shell is None:
        cleanup_sandbox(str(root))
        raise SandboxUnavailable("POSIX shell is required for namespace isolation")

    command = [
        unshare,
        "--user",
        "--map-root-user",
        "--mount",
        "--net",
        "--pid",
        "--ipc",
        "--uts",
        "--fork",
        shell,
        str(launcher),
    ]
    env["AI_AGENT_SANDBOX"] = "1"
    env["AI_AGENT_SANDBOX_ROOT"] = str(root)
    return command, env, str(root)


def cleanup_sandbox(root: str) -> None:
    root_path = Path(root)
    shutil.rmtree(root_path, ignore_errors=True)

    # The namespace launcher lives beside the temporary root and must be
    # removed separately. Otherwise every sandbox invocation can leak one
    # executable shell script into the host temporary directory.
    prefix = ".ai-agent-root-"
    name = root_path.name
    if not name.startswith(prefix):
        return
    suffix = name[len(prefix):]
    token = suffix.split("-", 1)[0]
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token.lower()):
        return
    launcher = root_path.parent / f".ai-agent-launcher-{token}.sh"
    try:
        launcher.unlink()
    except FileNotFoundError:
        pass
