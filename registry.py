from tools import (
    lihat,
    lokasi,
    siapa,
    buat_folder,
    buat_file,
    baca_file,
    tulis_file,
    cari_teks,
    patch_file,
    jalankan_python,
)

from execution.command import _run_command_unchecked
from security.guard import GuardEngine
from security.schema import SchemaValidationError, validate_tool_arguments
import os

from memory import (
    remember,
)

from memory_context import (
    recall_memory,
    search_memory_tool,
)


def _guarded_run_command(*, command, cwd, timeout=120.0):
    """Re-check process requests at the registered handler boundary."""
    workspace = os.environ.get("AI_AGENT_WORKSPACE_ROOT")
    if not workspace or not workspace.strip():
        return {
            "success": False,
            "status": "WORKSPACE_NOT_CONFIGURED",
            "exit_code": None,
            "stdout": "",
            "stderr": "AI_AGENT_WORKSPACE_ROOT must be configured; caller cwd is not trusted",
            "sandbox_mode": "not_started",
        }
    try:
        validate_tool_arguments(
            TOOL_REGISTRY["run_command"]["parameters"],
            {"command": command, "cwd": cwd, "timeout": timeout},
        )
    except SchemaValidationError as error:
        return {
            "success": False,
            "status": "SCHEMA_INVALID",
            "exit_code": None,
            "stdout": "",
            "stderr": str(error),
            "sandbox_mode": "not_started",
        }
    decision = GuardEngine(workspace).check(
        tool="run_command",
        arguments={"command": command, "cwd": cwd},
    )
    if not decision.allowed:
        return {
            "success": False,
            "status": "GUARD_DENIED",
            "exit_code": None,
            "stdout": "",
            "stderr": decision.reason,
            "sandbox_mode": "not_started",
        }
    return _run_command_unchecked(command=command, cwd=cwd, timeout=timeout)


TOOL_REGISTRY = {

    "lihat": {
        "capabilities": ["workspace.read"],
        "func": lihat,
        "permission": "safe",
        "description": "Menampilkan isi direktori kerja saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "lokasi": {
        "capabilities": ["workspace.read"],
        "func": lokasi,
        "permission": "safe",
        "description": "Menampilkan lokasi direktori kerja saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "siapa": {
        "capabilities": ["workspace.read"],
        "func": siapa,
        "permission": "safe",
        "description": "Menampilkan username pengguna Termux saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "cari_teks": {
        "capabilities": ["workspace.read"],
        "func": cari_teks,
        "permission": "safe",
        "description": "Mencari teks di dalam file workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "pola": {"type": "string"},
            },
            "required": ["query"],
        },
    },

    "patch_file": {
        "capabilities": ["workspace.write"],
        "func": patch_file,
        "permission": "confirm",
        "description": "Menerapkan penggantian teks yang exact dan bounded pada file workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "nama": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
                "expected_count": {"type": "integer"},
                "expected_sha256": {"type": "string"},
            },
            "required": ["nama", "old", "new"],
        },
    },

    "baca_file": {
        "capabilities": ["workspace.read"],
        "func": baca_file,
        "permission": "safe",
        "description": "Membaca isi sebuah file.",
        "parameters": {
            "type": "object",
            "properties": {
                "nama": {
                    "type": "string",
                    "description": "Nama atau path file yang ingin dibaca.",
                },
            },
            "required": ["nama"],
        },
    },

    "buat_folder": {
        "capabilities": ["workspace.write"],
        "func": buat_folder,
        "permission": "confirm",
        "description": "Membuat folder baru.",
        "parameters": {
            "type": "object",
            "properties": {
                "nama": {
                    "type": "string",
                    "description": "Nama folder yang ingin dibuat.",
                },
            },
            "required": ["nama"],
        },
    },

    "buat_file": {
        "capabilities": ["workspace.write"],
        "func": buat_file,
        "permission": "confirm",
        "description": "Membuat file kosong baru.",
        "parameters": {
            "type": "object",
            "properties": {
                "nama": {
                    "type": "string",
                    "description": "Nama file yang ingin dibuat.",
                },
            },
            "required": ["nama"],
        },
    },

    "tulis_file": {
        "capabilities": ["workspace.write"],
        "func": tulis_file,
        "permission": "confirm",
        "description": "Menulis atau mengganti isi sebuah file.",
        "parameters": {
            "type": "object",
            "properties": {
                "nama": {
                    "type": "string",
                    "description": "Nama atau path file.",
                },
                "isi": {
                    "type": "string",
                    "description": "Isi lengkap yang akan ditulis ke file.",
                },
                "expected_sha256": {
                    "type": "string",
                    "description": "SHA-256 snapshot dari baca_file; penulisan ditolak jika file sudah berubah.",
                },
            },
            "required": ["nama", "isi"],
        },
    },

    "run_command": {
        "capabilities": ["process.execute"],
        "func": _guarded_run_command,
        "permission": "confirm",
        "description": "Menjalankan satu command proyek melalui execution boundary yang dibatasi.",
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "command": {"type": "string", "maxLength": 4096},
                "cwd": {"type": "string", "maxLength": 2048},
                "timeout": {"type": "number", "minimum": 0.1, "maximum": 120},
            },
            "required": ["command", "cwd"],
        },
    },

    "jalankan_python": {
        "capabilities": ["process.execute"],
        "func": jalankan_python,
        "permission": "blocked",
        "description": "Dinonaktifkan: eksekusi interpreter tidak aman pada fallback Termux.",
        "parameters": {
            "type": "object",
            "properties": {
                "nama": {
                    "type": "string",
                    "description": "Nama atau path file Python yang ingin dijalankan.",
                },
            },
            "required": ["nama"],
        },
    },

    "remember": {
        "capabilities": ["workspace.write"],
        "func": remember,
        "permission": "confirm",
        "description": "Menyimpan informasi ke memory agent.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Nama atau kunci informasi yang ingin disimpan.",
                },
                "value": {
                    "type": "string",
                    "description": "Nilai informasi yang ingin disimpan.",
                },
            },
            "required": ["key", "value"],
        },
    },

    "recall": {
        "capabilities": ["workspace.read"],
        "func": recall_memory,
        "permission": "safe",
        "description": "Mengambil informasi dari memory agent berdasarkan key.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Kunci informasi yang ingin dicari.",
                },
            },
            "required": ["key"],
        },
    },

    "search_memory": {
        "capabilities": ["workspace.read"],
        "func": search_memory_tool,
        "permission": "safe",
        "description": "Mencari informasi di memory berdasarkan key atau value.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Teks yang ingin dicari di dalam memory.",
                },
            },
            "required": ["query"],
        },
    },
}


def get_tool(nama_tool):

    return TOOL_REGISTRY.get(
        nama_tool
    )


def get_tool_function(nama_tool):

    tool = get_tool(
        nama_tool
    )

    if tool is None:
        return None

    return tool["func"]


def get_tool_permission(nama_tool):

    tool = get_tool(
        nama_tool
    )

    if tool is None:
        return "blocked"

    return tool["permission"]



def get_tool_catalog():
    """Return non-executable tool metadata for planning and model context."""
    return {
        name: {
            "description": entry["description"],
            "parameters": entry["parameters"],
            "permission": entry["permission"],
            "capabilities": entry.get("capabilities", []),
        }
        for name, entry in TOOL_REGISTRY.items()
    }
