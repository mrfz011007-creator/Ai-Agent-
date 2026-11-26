import json

from registry import get_tool_permission
from security.redaction import redact_value


def get_permission(nama_tool):
    """Return the centrally registered permission class for a tool."""
    return get_tool_permission(nama_tool)


def minta_konfirmasi(nama_tool, args):
    """Require an explicit, tool-specific approval phrase."""
    print("\nPERSETUJUAN DIPERLUKAN")
    print(f"Tool: {nama_tool}")
    print(
        "Peringatan: persetujuan mengizinkan tool berjalan dengan hak aplikasi "
        "Termux; ini bukan isolasi filesystem atau jaringan."
    )
    rendered = json.dumps(redact_value(args), ensure_ascii=False, sort_keys=True)
    if len(rendered) > 2000:
        print("Persetujuan ditolak: argumen terlalu panjang untuk ditampilkan secara utuh.")
        return False
    print("Argumen:", rendered)
    expected = f"SETUJU {nama_tool}"
    jawaban = input(f"Ketik persis '{expected}' untuk melanjutkan: ").strip()
    return jawaban == expected
