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

from memory import (
    remember,
)

from memory_context import (
    recall_memory,
    search_memory_tool,
)


TOOL_REGISTRY = {

    "lihat": {
        "func": lihat,
        "permission": "safe",
        "description": "Menampilkan isi direktori kerja saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "lokasi": {
        "func": lokasi,
        "permission": "safe",
        "description": "Menampilkan lokasi direktori kerja saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "siapa": {
        "func": siapa,
        "permission": "safe",
        "description": "Menampilkan username pengguna Termux saat ini.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },

    "cari_teks": {
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
            },
            "required": ["nama", "isi"],
        },
    },

    "jalankan_python": {
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
        }
        for name, entry in TOOL_REGISTRY.items()
    }
