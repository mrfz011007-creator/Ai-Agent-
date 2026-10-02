from __future__ import annotations

import os
from pathlib import Path

from execution.command import run_command


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
        command=command,
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


def baca_file(nama):
    try:
        isi = path_aman(nama).read_text(encoding="utf-8")
        return {
            "status": "success",
            "success": True,
            "isi": isi,
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


def tulis_file(nama, isi):
    try:
        path_aman(nama).write_text(isi, encoding="utf-8")
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
