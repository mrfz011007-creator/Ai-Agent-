from __future__ import annotations

import hashlib
import os
import shlex
import tempfile
from pathlib import Path

from execution.command import run_command


DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SEARCH_RESULTS = 5000


def _max_file_bytes() -> int:
    value = int(os.environ.get("AI_AGENT_MAX_FILE_BYTES", str(DEFAULT_MAX_FILE_BYTES)))
    if value < 1:
        raise ValueError("AI_AGENT_MAX_FILE_BYTES must be positive")
    return value


def _max_search_results() -> int:
    value = int(os.environ.get("AI_AGENT_MAX_SEARCH_RESULTS", str(DEFAULT_MAX_SEARCH_RESULTS)))
    if value < 1:
        raise ValueError("AI_AGENT_MAX_SEARCH_RESULTS must be positive")
    return value



def _atomic_write_text(path: Path, content: str) -> None:
    """Replace a text file atomically within its parent directory."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    old_mode = None
    try:
        old_mode = path.stat().st_mode
    except FileNotFoundError:
        pass

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=False,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if old_mode is not None:
            os.chmod(temp_path, old_mode & 0o7777)
        os.replace(temp_path, path)
        if os.name == "posix":
            try:
                dir_fd = os.open(path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass

def workspace_root() -> Path:
    """Return the single configured workspace boundary used by legacy handlers."""
    return Path(
        os.environ.get("AI_AGENT_WORKSPACE_ROOT", os.getcwd())
    ).resolve()


def path_aman(nama: str) -> Path:
    """Resolve a path and reject traversal outside the configured workspace."""
    root = workspace_root()
    path = (root / nama).resolve()

    if path == root or root in path.parents:
        return path

    raise PermissionError(
        "Akses ditolak: path berada di luar workspace agent."
    )


def jalankan(command):
    """Run a command through the bounded command executor."""
    result = run_command(
        command=shlex.join(command) if not isinstance(command, str) else command,
        cwd=str(workspace_root()),
    )
    return {
        "success": result["success"],
        "status": result["status"],
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "returncode": result.get("exit_code"),
        **({"error": result["error"]} if result.get("error") else {}),
    }


def lihat():
    return jalankan(["ls", "-la"])


def lokasi():
    return jalankan(["pwd"])


def siapa():
    return jalankan(["whoami"])


def buat_folder(nama):
    try:
        path_aman(nama).mkdir()
        return {
            "status": "success",
            "success": True,
            "pesan": f"Folder berhasil dibuat: {nama}",
        }
    except FileExistsError:
        return {
            "status": "error",
            "success": False,
            "pesan": f"Folder sudah ada: {nama}",
        }
    except Exception as error:
        return {
            "status": "error",
            "success": False,
            "pesan": str(error),
        }


def buat_file(nama):
    try:
        path = path_aman(nama)
        path.touch(exist_ok=False)
        return {
            "status": "success",
            "success": True,
            "pesan": f"File berhasil dibuat: {nama}",
        }
    except FileExistsError:
        return {
            "status": "error",
            "success": False,
            "pesan": f"File sudah ada: {nama}",
        }
    except Exception as error:
        return {
            "status": "error",
            "success": False,
            "pesan": str(error),
        }


def cari_teks(query, pola="*.py"):
    """Search text inside workspace files without leaving the workspace."""
    query = str(query)
    if not query:
        raise ValueError("Query pencarian tidak boleh kosong.")

    root = workspace_root()
    hasil = []
    max_results = _max_search_results()
    max_bytes = _max_file_bytes()
    for path in root.rglob(pola):
        if len(hasil) >= max_results:
            break
        if not path.is_file():
            continue
        try:
            resolved = path.resolve()
            if not (resolved == root or root in resolved.parents):
                continue
            if resolved.stat().st_size > max_bytes:
                continue
            with resolved.open("r", encoding="utf-8") as stream:
                for nomor, line in enumerate(stream, 1):
                    if query.lower() in line.lower():
                        hasil.append(
                            {
                                "path": str(resolved.relative_to(root)),
                                "line": nomor,
                                "text": line.rstrip("\n"),
                            }
                        )
                        if len(hasil) >= max_results:
                            break
        except (OSError, UnicodeDecodeError):
            continue

    return {
        "status": "success",
        "success": True,
        "query": query,
        "hasil": hasil,
        "truncated": len(hasil) >= max_results,
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patch_file(nama, old, new, expected_count=1, expected_sha256=None):
    """Apply an exact bounded text replacement with optional optimistic locking."""
    if not isinstance(old, str) or not old:
        raise ValueError("Teks lama tidak boleh kosong.")
    if not isinstance(new, str):
        raise TypeError("Teks baru harus berupa string.")
    if expected_count < 1:
        raise ValueError("expected_count harus >= 1.")

    path = path_aman(nama)
    max_bytes = _max_file_bytes()
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_NOT_FOUND",
            "pesan": f"File tidak ditemukan: {nama}",
        }
    except IsADirectoryError:
        return {
            "status": "error",
            "success": False,
            "code": "NOT_A_FILE",
            "pesan": f"Target bukan file: {nama}",
        }
    if len(raw) > max_bytes:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_TOO_LARGE",
            "pesan": "File melebihi batas ukuran patch.",
        }
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_NOT_UTF8",
            "pesan": "File target bukan UTF-8 yang valid.",
        }
    current_sha256 = _sha256_text(text)
    if expected_sha256 is not None and current_sha256 != expected_sha256:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_CHANGED",
            "pesan": "Patch ditolak: isi file berubah sejak snapshot terakhir.",
            "current_sha256": current_sha256,
        }

    count = text.count(old)
    if count != expected_count:
        return {
            "status": "error",
            "success": False,
            "pesan": f"Patch ditolak: ditemukan {count} kecocokan, diharapkan {expected_count}.",
        }

    updated_text = text.replace(old, new)
    if len(updated_text.encode("utf-8")) > max_bytes:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_TOO_LARGE",
            "pesan": "Hasil patch melebihi batas ukuran file.",
        }
    _atomic_write_text(path, updated_text)
    return {
        "status": "success",
        "success": True,
        "pesan": f"Patch diterapkan: {nama}",
        "sha256": _sha256_text(updated_text),
    }


def baca_file(nama):
    try:
        path = path_aman(nama)
        if path.stat().st_size > _max_file_bytes():
            return {
                "status": "error",
                "success": False,
                "code": "FILE_TOO_LARGE",
                "pesan": "File melebihi batas ukuran baca.",
            }
        isi = path.read_text(encoding="utf-8")
        return {
            "status": "success",
            "success": True,
            "isi": isi,
            "sha256": _sha256_text(isi),
            "size": len(isi.encode("utf-8")),
        }
    except FileNotFoundError:
        return {
            "status": "error",
            "success": False,
            "pesan": f"File tidak ditemukan: {nama}",
        }
    except PermissionError:
        return {
            "status": "error",
            "success": False,
            "pesan": f"Tidak punya izin membaca file: {nama}",
        }
    except Exception as error:
        return {
            "status": "error",
            "success": False,
            "pesan": str(error),
        }


def tulis_file(nama, isi, expected_sha256=None):
    try:
        path = path_aman(nama)
        if not isinstance(isi, str):
            raise TypeError("Isi file harus berupa string.")
        if len(isi.encode("utf-8")) > _max_file_bytes():
            return {
                "status": "error",
                "success": False,
                "code": "FILE_TOO_LARGE",
                "pesan": "File melebihi batas ukuran tulis.",
            }
        if expected_sha256 is not None:
            if not path.exists():
                return {
                    "status": "error",
                    "success": False,
                    "code": "FILE_CHANGED",
                    "pesan": "Penulisan ditolak: file yang diharapkan tidak ada.",
                }
            if path.stat().st_size > _max_file_bytes():
                return {
                    "status": "error",
                    "success": False,
                    "code": "FILE_TOO_LARGE",
                    "pesan": "File saat ini melebihi batas ukuran snapshot.",
                }
            current = path.read_text(encoding="utf-8")
            current_sha256 = _sha256_text(current)
            if current_sha256 != expected_sha256:
                return {
                    "status": "error",
                    "success": False,
                    "code": "FILE_CHANGED",
                    "pesan": "Penulisan ditolak: isi file berubah sejak snapshot terakhir.",
                    "current_sha256": current_sha256,
                }
        _atomic_write_text(path, isi)
        return {
            "status": "success",
            "success": True,
            "pesan": f"File berhasil ditulis: {nama}",
        }
    except PermissionError:
        return {
            "status": "error",
            "success": False,
            "pesan": f"Tidak punya izin menulis file: {nama}",
        }
    except Exception as error:
        return {
            "status": "error",
            "success": False,
            "pesan": str(error),
        }


def jalankan_python(nama):
    try:
        path = path_aman(nama)
        if path.suffix.lower() != ".py":
            raise ValueError("Hanya file Python (.py) yang boleh dijalankan.")
        return jalankan(["python", str(path)])
    except Exception as error:
        return {
            "status": "error",
            "success": False,
            "pesan": str(error),
        }
