from __future__ import annotations

import fnmatch
import hashlib
import os
import stat
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Android/POSIX builds normally provide fcntl
    fcntl = None


DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024
HARD_MAX_FILE_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_SEARCH_RESULTS = 500
HARD_MAX_SEARCH_RESULTS = 2000
DEFAULT_MAX_SEARCH_FILES = 2000
HARD_MAX_SEARCH_FILES = 5000
DEFAULT_MAX_SEARCH_BYTES = 64 * 1024 * 1024
HARD_MAX_SEARCH_BYTES = 128 * 1024 * 1024
MAX_SEARCH_ENTRIES = 10000
MAX_SEARCH_DIRECTORIES = 256
MAX_SEARCH_DEPTH = 24
MAX_PATH_CHARS = 1024
MAX_QUERY_CHARS = 4096
MAX_PATTERN_CHARS = 128
MAX_RESULT_LINE_CHARS = 1000


class WorkspaceSecurityError(PermissionError):
    """A path cannot be accessed without following a link or leaving the workspace."""


class FileTooLargeError(ValueError):
    """A workspace file exceeds the configured hard read/write bound."""


class FileChangedError(RuntimeError):
    """The target changed after its inspected snapshot was obtained."""

    def __init__(self, current_sha256: str | None = None):
        super().__init__("FILE_CHANGED")
        self.current_sha256 = current_sha256


def _env_bounded_int(name: str, default: int, hard_max: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return min(value, hard_max)


def max_file_bytes() -> int:
    return _env_bounded_int(
        "AI_AGENT_MAX_FILE_BYTES", DEFAULT_MAX_FILE_BYTES, HARD_MAX_FILE_BYTES
    )


def max_search_results() -> int:
    return _env_bounded_int(
        "AI_AGENT_MAX_SEARCH_RESULTS",
        DEFAULT_MAX_SEARCH_RESULTS,
        HARD_MAX_SEARCH_RESULTS,
    )


def max_search_files() -> int:
    return _env_bounded_int(
        "AI_AGENT_MAX_SEARCH_FILES",
        DEFAULT_MAX_SEARCH_FILES,
        HARD_MAX_SEARCH_FILES,
    )


def max_search_bytes() -> int:
    return _env_bounded_int(
        "AI_AGENT_MAX_SEARCH_BYTES",
        DEFAULT_MAX_SEARCH_BYTES,
        HARD_MAX_SEARCH_BYTES,
    )


def _path_parts(name: str) -> tuple[str, ...]:
    if not isinstance(name, str):
        raise WorkspaceSecurityError("Workspace path must be a string")
    if not name or len(name) > MAX_PATH_CHARS or "\x00" in name:
        raise WorkspaceSecurityError("Workspace path is empty or too long")
    if name.startswith("/") or "\\" in name:
        raise WorkspaceSecurityError("Absolute or ambiguous workspace paths are denied")
    parts = tuple(name.split("/"))
    if any(part in ("", ".", "..") for part in parts):
        raise WorkspaceSecurityError("Workspace traversal components are denied")
    return parts


def _nofollow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", None)
    if flag is None:
        raise WorkspaceSecurityError("O_NOFOLLOW is unavailable; file access fails closed")
    return flag


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | _nofollow_flag()
    )


def _open_root(root: str | Path) -> int:
    resolved = Path(root).resolve(strict=True)
    fd = os.open(resolved, _directory_flags())
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise WorkspaceSecurityError("Workspace root is not a directory")
    return fd


def _open_parent(root: str | Path, name: str) -> tuple[int, str]:
    parts = _path_parts(name)
    current_fd = _open_root(root)
    try:
        for component in parts[:-1]:
            child_fd = os.open(component, _directory_flags(), dir_fd=current_fd)
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                os.close(child_fd)
                raise WorkspaceSecurityError("Workspace parent is not a directory")
            os.close(current_fd)
            current_fd = child_fd
        return current_fd, parts[-1]
    except Exception:
        os.close(current_fd)
        raise


def _open_regular_file(parent_fd: int, leaf: str, flags: int, mode: int = 0o600) -> int:
    fd = os.open(
        leaf,
        flags | _nofollow_flag() | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
        mode,
        dir_fd=parent_fd,
    )
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise WorkspaceSecurityError("Only regular workspace files are permitted")
    if info.st_nlink != 1:
        os.close(fd)
        raise WorkspaceSecurityError("Hard-linked workspace files are denied")
    return fd


def _read_fd(fd: int, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= limit:
        chunk = os.read(fd, min(65536, limit + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            raise FileTooLargeError("Workspace file exceeds the maximum size")
    return b"".join(chunks)


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        count = os.write(fd, view[written:])
        if count <= 0:
            raise OSError("Short write while updating workspace file")
        written += count


def create_directory_secure(root: str | Path, name: str) -> None:
    parent_fd, leaf = _open_parent(root, name)
    try:
        os.mkdir(leaf, mode=0o700, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)


def create_file_secure(root: str | Path, name: str) -> None:
    parent_fd, leaf = _open_parent(root, name)
    fd = None
    try:
        fd = os.open(
            leaf,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | _nofollow_flag()
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        os.fsync(fd)
        os.fsync(parent_fd)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def read_file_secure(root: str | Path, name: str, limit: int) -> bytes:
    parent_fd, leaf = _open_parent(root, name)
    fd = None
    try:
        fd = _open_regular_file(parent_fd, leaf, os.O_RDONLY)
        if os.fstat(fd).st_size > limit:
            raise FileTooLargeError("Workspace file exceeds the maximum size")
        return _read_fd(fd, limit)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def write_file_secure(
    root: str | Path,
    name: str,
    payload: bytes,
    limit: int,
    expected_sha256: str | None = None,
) -> None:
    if not isinstance(payload, bytes):
        raise TypeError("Workspace payload must be bytes")
    if len(payload) > limit:
        raise FileTooLargeError("Workspace write exceeds the maximum size")

    parent_fd, leaf = _open_parent(root, name)
    fd = None
    created = False
    try:
        try:
            fd = _open_regular_file(parent_fd, leaf, os.O_RDWR)
        except FileNotFoundError:
            if expected_sha256 is not None:
                raise FileChangedError(None)
            fd = os.open(
                leaf,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | _nofollow_flag()
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
            created = True

        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise WorkspaceSecurityError("Only unlinked regular files may be updated")
        if not created and info.st_size > limit:
            raise FileTooLargeError("Existing workspace file exceeds the maximum size")

        if not created and fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        if expected_sha256 is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            current = _read_fd(fd, limit)
            current_sha256 = hashlib.sha256(current).hexdigest()
            if current_sha256 != expected_sha256:
                raise FileChangedError(current_sha256)

        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        _write_all(fd, payload)
        os.fsync(fd)
        os.fsync(parent_fd)
    except FileExistsError:
        # A concurrent creator won the O_EXCL race. Do not retry through a
        # pathname lookup, because the new entry may be a symlink.
        raise
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def search_text_secure(
    root: str | Path,
    query: str,
    pattern: str,
    *,
    per_file_limit: int,
    max_results: int,
    max_files: int,
    max_total_bytes: int,
) -> dict:
    if not isinstance(query, str) or not query or len(query) > MAX_QUERY_CHARS:
        raise ValueError("Search query must contain 1–4096 characters")
    if (
        not isinstance(pattern, str)
        or not pattern
        or len(pattern) > MAX_PATTERN_CHARS
        or "/" in pattern
        or "\\" in pattern
    ):
        raise ValueError("Search pattern must be a filename glob of at most 128 characters")

    root_fd = _open_root(root)
    directories: list[tuple[int, str, int]] = [(root_fd, "", 0)]
    results: list[dict] = []
    entries_seen = 0
    files_seen = 0
    bytes_seen = 0
    truncated = False
    stop = False
    query_folded = query.casefold()

    try:
        while directories and not stop:
            dir_fd, relative_dir, depth = directories.pop()
            try:
                scan_fd = os.dup(dir_fd)
                try:
                    with os.scandir(scan_fd) as entries:
                        for entry in entries:
                            entries_seen += 1
                            if entries_seen > MAX_SEARCH_ENTRIES:
                                truncated = True
                                stop = True
                                break

                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    if depth < MAX_SEARCH_DEPTH:
                                        if len(directories) >= MAX_SEARCH_DIRECTORIES:
                                            truncated = True
                                            continue
                                        try:
                                            child_fd = os.open(
                                                entry.name,
                                                _directory_flags(),
                                                dir_fd=dir_fd,
                                            )
                                            if stat.S_ISDIR(os.fstat(child_fd).st_mode):
                                                child_relative = (
                                                    f"{relative_dir}/{entry.name}"
                                                    if relative_dir
                                                    else entry.name
                                                )
                                                directories.append(
                                                    (child_fd, child_relative, depth + 1)
                                                )
                                            else:
                                                os.close(child_fd)
                                        except OSError:
                                            # Symlinks and concurrently replaced directories
                                            # are skipped rather than followed.
                                            continue
                                    else:
                                        truncated = True
                                    continue
                            except OSError:
                                continue

                            if not fnmatch.fnmatchcase(entry.name, pattern):
                                continue

                            files_seen += 1
                            if files_seen > max_files:
                                truncated = True
                                stop = True
                                break

                            file_fd = None
                            try:
                                file_fd = _open_regular_file(
                                    dir_fd, entry.name, os.O_RDONLY
                                )
                                info = os.fstat(file_fd)
                                if info.st_size > per_file_limit:
                                    truncated = True
                                    continue
                                remaining = max_total_bytes - bytes_seen
                                if remaining <= 0:
                                    truncated = True
                                    stop = True
                                    break
                                raw = _read_fd(file_fd, min(per_file_limit, remaining))
                                bytes_seen += len(raw)
                                content = raw.decode("utf-8")
                            except FileTooLargeError:
                                truncated = True
                                stop = True
                                break
                            except (OSError, UnicodeDecodeError, WorkspaceSecurityError):
                                continue
                            finally:
                                if file_fd is not None:
                                    os.close(file_fd)

                            relative_path = (
                                f"{relative_dir}/{entry.name}"
                                if relative_dir
                                else entry.name
                            )
                            for line_number, line in enumerate(content.splitlines(), 1):
                                if query_folded in line.casefold():
                                    results.append(
                                        {
                                            "path": relative_path,
                                            "line": line_number,
                                            "text": line[:MAX_RESULT_LINE_CHARS],
                                        }
                                    )
                                    if len(results) >= max_results:
                                        truncated = True
                                        stop = True
                                        break
                            if stop:
                                break
                finally:
                    try:
                        os.close(scan_fd)
                    except OSError:
                        pass
            except OSError:
                continue
            finally:
                os.close(dir_fd)
    finally:
        for fd, _relative, _depth in directories:
            try:
                os.close(fd)
            except OSError:
                pass

    return {
        "hasil": results,
        "truncated": truncated,
        "files_scanned": min(files_seen, max_files),
        "bytes_scanned": bytes_seen,
    }
