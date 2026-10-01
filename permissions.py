import json

from registry import get_tool_permission


def get_permission(nama_tool):
    """
    Mengambil permission dari Tool Registry.

    safe    = boleh dijalankan otomatis
    confirm = harus meminta persetujuan pengguna
    blocked = tidak diizinkan
    """

    return get_tool_permission(
        nama_tool
    )


def minta_konfirmasi(nama_tool, args):
    """
    Meminta persetujuan pengguna sebelum
    menjalankan tool yang membutuhkan konfirmasi.
    """

    print("\n🛡️ Tool membutuhkan konfirmasi.")

    print(
        f"🔧 Tool: {nama_tool}"
    )

    print(
        "📦 Argumen:",
        json.dumps(
            args,
            ensure_ascii=False
        )
    )

    jawaban = input(
        "\nIzinkan tool ini dijalankan? [y/N]: "
    ).strip().lower()

    return jawaban in ["y", "yes"]
