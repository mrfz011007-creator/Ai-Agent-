from intent_router import (
    deteksi_intent,
)

from registry import (
    get_tool_function,
)


# ============================================================
# EKSEKUSI TOOL LOKAL
# ============================================================

def jalankan_tool_lokal(
    nama_tool,
    args
):
    """
    Menjalankan tool lokal melalui
    Tool Registry.
    """

    tool = get_tool_function(
        nama_tool
    )

    if tool is None:

        return {
            "status": "error",
            "pesan": (
                f"Tool lokal tidak ditemukan: "
                f"{nama_tool}"
            )
        }

    try:

        hasil = tool(
            **args
        )

        return {
            "status": "local_success",
            "tool": nama_tool,
            "hasil": hasil
        }

    except Exception as error:

        return {
            "status": "local_error",
            "tool": nama_tool,
            "pesan": str(error)
        }


# ============================================================
# LOCAL-FIRST ENTRY POINT
# ============================================================

def jalankan_lokal(perintah):
    """
    Menjalankan perintah menggunakan
    Intent Router.

    Alur:

        perintah
            ↓
        deteksi_intent()
            ↓
        intent ditemukan
            ↓
        tool lokal
            ↓
        hasil

    Jika intent tidak ditemukan:

        return None

    agar Agent dapat meneruskannya
    ke Gemini.
    """

    hasil_intent = deteksi_intent(
        perintah
    )

    # ========================================================
    # TIDAK ADA INTENT
    # ========================================================

    if hasil_intent is None:

        return None

    # ========================================================
    # INTENT DITEMUKAN
    # ========================================================

    nama_tool = hasil_intent["tool"]

    args = {}

    # ========================================================
    # MEMORY
    # ========================================================

    if hasil_intent["jenis"] == "memory":

        args = {
            "key": hasil_intent["key"]
        }

    # ========================================================
    # JALANKAN TOOL
    # ========================================================

    return jalankan_tool_lokal(
        nama_tool,
        args
    )
