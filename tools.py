from __future__ import annotations

import ast
import hashlib
import os
import shlex
from pathlib import Path

from execution.command import run_command
from security.guard import GuardEngine
from security.workspace import (
    FileChangedError,
    FileTooLargeError,
    create_directory_secure,
    create_file_secure,
    max_file_bytes,
    max_search_bytes,
    max_search_files,
    max_search_results,
    read_file_secure,
    search_text_secure,
    write_file_secure,
)

DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_SEARCH_RESULTS = 500
MAX_STATIC_PYTHON_VALIDATION_BYTES = 256 * 1024
MAX_VERIFIED_FILE_CONTENT_CHARS = 16000


def workspace_root() -> Path:
    """Return the configured workspace root; filesystem operations use dirfds."""
    configured = os.environ.get("AI_AGENT_WORKSPACE_ROOT")
    if not configured or not configured.strip():
        raise RuntimeError("AI_AGENT_WORKSPACE_ROOT wajib dikonfigurasi; cwd pemanggil tidak dipercaya.")
    return Path(configured).resolve(strict=True)


def path_aman(nama: str) -> Path:
    """Return a lexical workspace path for display/compatibility, not for I/O.

    Actual reads and writes use descriptor-relative helpers with O_NOFOLLOW.
    """
    if not isinstance(nama, str) or not nama or "\x00" in nama or "\\" in nama:
        raise PermissionError("Path workspace tidak valid.")
    relative = Path(nama)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        raise PermissionError("Akses ditolak: path harus relatif dan berada di workspace.")
    if len(nama) > 1024:
        raise PermissionError("Path workspace terlalu panjang.")
    return workspace_root().joinpath(*relative.parts)


def _max_file_bytes() -> int:
    return max_file_bytes()


def jalankan(command):
    """Run a command only after checking the shared process guard."""
    command_text = shlex.join(command) if not isinstance(command, str) else command
    cwd = str(workspace_root())
    decision = GuardEngine(workspace_root()).check(
        tool="run_command",
        arguments={"command": command_text, "cwd": cwd},
    )
    if not decision.allowed:
        return {
            "success": False,
            "status": "GUARD_DENIED",
            "stdout": "",
            "stderr": decision.reason,
            "returncode": None,
        }
    result = run_command(command=command_text, cwd=cwd)
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
        create_directory_secure(workspace_root(), nama)
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
        create_file_secure(workspace_root(), nama)
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


def buat_file_terverifikasi(nama, isi):
    """Create a new file, write exact UTF-8 content, read it back, and compare bytes.

    Existing paths are never overwritten. All file I/O uses the secure workspace
    helpers, including descriptor-relative no-follow operations.
    """
    if not isinstance(isi, str):
        return {"status": "error", "success": False, "verified": False,
                "code": "INVALID_CONTENT", "pesan": "Isi file harus berupa teks."}
    if len(isi) > MAX_VERIFIED_FILE_CONTENT_CHARS:
        return {"status": "error", "success": False, "verified": False,
                "code": "VERIFIED_CONTENT_TOO_LARGE",
                "pesan": f"Konten dibatasi hingga {MAX_VERIFIED_FILE_CONTENT_CHARS} karakter."}

    try:
        root = workspace_root()
        limit = _max_file_bytes()
        payload = isi.encode("utf-8")
        if len(payload) > limit:
            raise FileTooLargeError("Workspace write exceeds the configured maximum size")
        create_file_secure(root, nama)
    except FileExistsError:
        return {"status": "error", "success": False, "verified": False,
                "code": "FILE_ALREADY_EXISTS",
                "pesan": f"File sudah ada; tidak ditimpa: {nama}"}
    except FileTooLargeError:
        return {"status": "error", "success": False, "verified": False,
                "code": "FILE_TOO_LARGE", "pesan": "Konten melebihi batas ukuran workspace."}
    except FileNotFoundError:
        return {"status": "error", "success": False, "verified": False,
                "code": "WORKSPACE_PARENT_NOT_FOUND",
                "pesan": f"Folder induk tidak ditemukan untuk: {nama}"}
    except PermissionError as error:
        return {"status": "error", "success": False, "verified": False,
                "code": "WORKSPACE_ACCESS_DENIED", "pesan": str(error)}
    except Exception as error:
        return {"status": "error", "success": False, "verified": False,
                "code": "FILE_CREATE_FAILED",
                "pesan": f"{type(error).__name__}: {error}"}

    steps = {"created": True, "written": False, "read_back": False, "exact_match": False}
    empty_sha256 = hashlib.sha256(b"").hexdigest()
    try:
        # Only the empty file created above may be replaced by this write.
        write_file_secure(root, nama, payload, limit, expected_sha256=empty_sha256)
        steps["written"] = True
        actual_bytes = read_file_secure(root, nama, limit)
        steps["read_back"] = True
        actual = actual_bytes.decode("utf-8")
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        actual_sha256 = hashlib.sha256(actual_bytes).hexdigest()
        exact_match = actual_bytes == payload
        steps["exact_match"] = exact_match
        result = {
            "status": "success" if exact_match else "error",
            "success": exact_match,
            "verified": exact_match,
            "code": "CONTENT_MATCH" if exact_match else "CONTENT_MISMATCH",
            "path": nama,
            "isi_aktual": actual,
            "expected_sha256": expected_sha256,
            "actual_sha256": actual_sha256,
            "size": len(actual_bytes),
            "steps": steps,
        }
        if not exact_match:
            result["pesan"] = "Isi hasil pembacaan tidak sama persis dengan konten target."
        return result
    except FileChangedError as error:
        return {"status": "error", "success": False, "verified": False,
                "code": "FILE_CHANGED",
                "pesan": "File berubah di antara tahap tulis dan verifikasi.",
                "current_sha256": error.current_sha256, "path": nama, "steps": steps}
    except FileTooLargeError:
        return {"status": "error", "success": False, "verified": False,
                "code": "FILE_TOO_LARGE",
                "pesan": "File melebihi batas ukuran saat tulis atau baca ulang.",
                "path": nama, "steps": steps}
    except FileNotFoundError:
        return {"status": "error", "success": False, "verified": False,
                "code": "FILE_DISAPPEARED",
                "pesan": "File tidak ditemukan saat tulis atau baca ulang.",
                "path": nama, "steps": steps}
    except PermissionError as error:
        return {"status": "error", "success": False, "verified": False,
                "code": "WORKSPACE_ACCESS_DENIED", "pesan": str(error),
                "path": nama, "steps": steps}
    except UnicodeDecodeError:
        return {"status": "error", "success": False, "verified": False,
                "code": "FILE_ENCODING_INVALID",
                "pesan": "Hasil pembacaan bukan teks UTF-8 valid.",
                "path": nama, "steps": steps}
    except Exception as error:
        return {"status": "error", "success": False, "verified": False,
                "code": "FILE_VERIFICATION_FAILED",
                "pesan": f"{type(error).__name__}: {error}",
                "path": nama, "steps": steps}


def cari_teks(query, pola="*.py"):
    """Search regular workspace files without following links and with hard caps."""
    limits = {
        "per_file_limit": _max_file_bytes(),
        "max_results": max_search_results(),
        "max_files": max_search_files(),
        "max_total_bytes": max_search_bytes(),
    }
    result = search_text_secure(
        workspace_root(),
        query,
        pola,
        **limits,
    )
    return {
        "status": "success",
        "success": True,
        "query": query,
        "hasil": result["hasil"],
        "truncated": result["truncated"],
        "files_scanned": result["files_scanned"],
        "bytes_scanned": result["bytes_scanned"],
    }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patch_file(nama, old, new, expected_count=1, expected_sha256=None):
    """Apply an exact, size-bounded patch to the already-open regular file."""
    if not isinstance(old, str) or not old:
        raise ValueError("Teks lama tidak boleh kosong.")
    if not isinstance(new, str):
        raise TypeError("Teks baru harus berupa string.")
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count < 1
    ):
        raise ValueError("expected_count harus berupa integer >= 1.")

    limit = _max_file_bytes()
    # Bound attacker-controlled patch strings before count/replace allocations.
    if len(old) > limit or len(new) > limit:
        return {
            "status": "error",
            "success": False,
            "code": "PATCH_INPUT_TOO_LARGE",
            "pesan": "Input patch melebihi batas karakter yang diizinkan.",
        }
    root = workspace_root()
    try:
        raw = read_file_secure(root, nama, limit)
    except FileNotFoundError:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_NOT_FOUND",
            "pesan": f"File tidak ditemukan: {nama}",
        }
    except FileTooLargeError:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_TOO_LARGE",
            "pesan": "File melebihi batas ukuran patch.",
        }
    except OSError as error:
        return {
            "status": "error",
            "success": False,
            "code": "WORKSPACE_ACCESS_DENIED",
            "pesan": str(error),
        }

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_ENCODING_INVALID",
            "pesan": "File bukan teks UTF-8 yang valid.",
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

    # Estimate the resulting character count before allocating the replacement.
    projected_chars = len(text) - count * len(old) + count * len(new)
    if projected_chars > limit:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_TOO_LARGE",
            "pesan": "Hasil patch diperkirakan melebihi batas ukuran file.",
        }
    updated_text = text.replace(old, new)
    updated_bytes = updated_text.encode("utf-8")
    if len(updated_bytes) > limit:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_TOO_LARGE",
            "pesan": "Hasil patch melebihi batas ukuran file.",
        }

    try:
        # Always compare against the snapshot read above, even when callers did
        # not supply an expected hash. The check happens on the same no-follow fd
        # that is subsequently updated, closing the pathname TOCTOU window.
        write_file_secure(
            root,
            nama,
            updated_bytes,
            limit,
            expected_sha256=current_sha256,
        )
    except FileChangedError as error:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_CHANGED",
            "pesan": "Patch ditolak: file berubah selama proses patch.",
            "current_sha256": error.current_sha256,
        }
    except FileTooLargeError:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_TOO_LARGE",
            "pesan": "File melebihi batas ukuran patch.",
        }
    except OSError as error:
        return {
            "status": "error",
            "success": False,
            "code": "WORKSPACE_ACCESS_DENIED",
            "pesan": str(error),
        }
    return {
        "status": "success",
        "success": True,
        "pesan": f"Patch diterapkan: {nama}",
        "sha256": _sha256_text(updated_text),
    }


def baca_file(nama):
    try:
        raw = read_file_secure(workspace_root(), nama, _max_file_bytes())
        isi = raw.decode("utf-8")
        return {
            "status": "success",
            "success": True,
            "isi": isi,
            "sha256": _sha256_text(isi),
            "size": len(raw),
        }
    except FileTooLargeError:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_TOO_LARGE",
            "pesan": "File melebihi batas ukuran baca.",
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
        if not isinstance(isi, str):
            raise TypeError("Isi file harus berupa string.")
        limit = _max_file_bytes()
        # Check character count before allocating the UTF-8 byte representation.
        if len(isi) > limit:
            raise FileTooLargeError("Workspace write exceeds the maximum size")
        payload = isi.encode("utf-8")
        if len(payload) > limit:
            raise FileTooLargeError("Workspace write exceeds the maximum size")
        write_file_secure(
            workspace_root(),
            nama,
            payload,
            limit,
            expected_sha256=expected_sha256,
        )
        return {
            "status": "success",
            "success": True,
            "pesan": f"File berhasil ditulis: {nama}",
        }
    except FileTooLargeError:
        return {
            "status": "error",
            "success": False,
            "code": "FILE_TOO_LARGE",
            "pesan": "File melebihi batas ukuran tulis.",
        }
    except FileChangedError as error:
        result = {
            "status": "error",
            "success": False,
            "code": "FILE_CHANGED",
            "pesan": "Penulisan ditolak: file berubah sejak snapshot terakhir.",
        }
        if error.current_sha256 is not None:
            result["current_sha256"] = error.current_sha256
        return result
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



def validasi_python(nama, fungsi_wajib=None):
    """Validate Python syntax and required top-level definitions without executing code."""
    if fungsi_wajib is None:
        fungsi_wajib = []
    if (
        not isinstance(fungsi_wajib, list)
        or len(fungsi_wajib) > 64
        or any(not isinstance(item, str) or not item.isidentifier() for item in fungsi_wajib)
    ):
        return {
            "status": "error",
            "success": False,
            "code": "VALIDATION_ARGUMENT_INVALID",
            "pesan": "fungsi_wajib harus berupa daftar nama fungsi/class yang valid.",
        }
    try:
        raw = read_file_secure(
            workspace_root(), nama,
            min(_max_file_bytes(), MAX_STATIC_PYTHON_VALIDATION_BYTES),
        )
        source = raw.decode("utf-8")
    except FileTooLargeError:
        return {"status": "error", "success": False, "code": "FILE_TOO_LARGE", "pesan": "File melebihi batas validasi 256 KiB."}
    except UnicodeDecodeError:
        return {"status": "error", "success": False, "code": "FILE_ENCODING_INVALID", "pesan": "File bukan UTF-8 valid."}
    except FileNotFoundError:
        return {"status": "error", "success": False, "code": "FILE_NOT_FOUND", "pesan": f"File tidak ditemukan: {nama}"}
    except Exception as error:
        return {"status": "error", "success": False, "code": "WORKSPACE_ACCESS_DENIED", "pesan": str(error)}

    try:
        tree = ast.parse(source, filename=nama, mode="exec")
    except (SyntaxError, RecursionError) as error:
        return {
            "status": "error",
            "success": False,
            "code": "PYTHON_SYNTAX_INVALID",
            "pesan": (
                f"Syntax error pada baris {error.lineno}: {error.msg}"
                if isinstance(error, SyntaxError)
                else "Struktur sintaks terlalu dalam untuk divalidasi."
            ),
            "line": error.lineno if isinstance(error, SyntaxError) else None,
        }

    definitions = [
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    missing = sorted(set(fungsi_wajib) - set(definitions))
    if missing:
        return {
            "status": "error",
            "success": False,
            "code": "REQUIRED_DEFINITION_MISSING",
            "pesan": "Definisi wajib tidak ditemukan.",
            "missing": missing,
            "definitions": definitions,
        }
    return {
        "status": "success",
        "success": True,
        "code": "PYTHON_SYNTAX_VALID",
        "pesan": "Sintaks Python valid; kode tidak dieksekusi.",
        "definitions": definitions,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def jalankan_python(nama):
    """Refuse direct script execution; Termux does not isolate hostile code."""
    return {
        "status": "error",
        "success": False,
        "pesan": (
            "Eksekusi file Python dinonaktifkan melalui agent karena Termux "
            "tidak menyediakan isolasi filesystem dan jaringan yang memadai."
        ),
    }
