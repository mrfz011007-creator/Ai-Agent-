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

from execution.command import run_command

from memory import (
    invalidate_memory,
    remember,
)

from memory_context import (
    recall_memory,
    search_memory_tool,
)


TOOL_REGISTRY = {

    "lihat": {
        "idempotent": True,        "capabilities": ["workspace.read"],
        "func": lihat,
        "permission": "safe",
        "description": "Menampilkan isi direktori kerja saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "lokasi": {
        "idempotent": True,        "capabilities": ["workspace.read"],
        "func": lokasi,
        "permission": "safe",
        "description": "Menampilkan lokasi direktori kerja saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "siapa": {
        "idempotent": True,        "capabilities": ["workspace.read"],
        "func": siapa,
        "permission": "safe",
        "description": "Menampilkan username pengguna Termux saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "cari_teks": {
        "idempotent": True,        "capabilities": ["workspace.read"],
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
        "idempotent": True,        "capabilities": ["workspace.read"],
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
        "func": run_command,
        "permission": "confirm",
        "description": "Menjalankan satu command proyek melalui execution boundary yang dibatasi.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "cwd": {"type": "string"},
            },
            "required": ["command"],
        },
    },

    "jalankan_python": {
        "capabilities": ["process.execute"],
        "func": jalankan_python,
        "permission": "confirm",
        "description": "Menjalankan file Python.",
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
                    "description": "Nilai informasi yang ingin disimpan.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["fact", "decision", "experience", "preference"],
                },
                "source": {
                    "description": "Provenance sumber memory, misalnya user, agent, tool, atau dokumen.",
                },
                "project_id": {
                    "type": "string",
                },
                "task_id": {
                    "type": "string",
                },
                "context": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["key", "value"],
        },
    },

    "invalidate_memory": {
        "capabilities": ["workspace.write"],
        "func": invalidate_memory,
        "permission": "confirm",
        "description": "Menonaktifkan versi memory aktif tanpa menghapus histori atau provenance.",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                },
                "project_id": {
                    "type": "string",
                },
                "task_id": {
                    "type": "string",
                },
                "kind": {
                    "type": "string",
                    "enum": ["fact", "decision", "experience", "preference"],
                },
                "reason": {
                    "type": "string",
                },
            },
            "required": ["key", "reason"],
        },
    },

    "recall": {
        "idempotent": True,        "capabilities": ["workspace.read"],
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
                "project_id": {"type": "string"},
                "task_id": {"type": "string"},
                "context": {"type": "array", "items": {"type": "string"}},
                "kind": {
                    "type": "string",
                    "enum": ["fact", "decision", "experience", "preference"],
                },
            },
            "required": ["key"],
        },
    },

    "search_memory": {
        "idempotent": True,        "capabilities": ["workspace.read"],
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
                "project_id": {"type": "string"},
                "task_id": {"type": "string"},
                "context": {"type": "array", "items": {"type": "string"}},
                "kinds": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["fact", "decision", "experience", "preference"],
                    },
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
            "idempotent": entry.get("idempotent", False),
        }
        for name, entry in TOOL_REGISTRY.items()
    }
