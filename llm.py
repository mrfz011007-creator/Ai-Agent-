import os
import time

from google import genai
from google.genai import types

from registry import TOOL_REGISTRY

from key_manager import KeyManager


# ============================================================
# KEY MANAGER
# ============================================================

key_manager = KeyManager()


# ============================================================
# SYSTEM INSTRUCTION
# ============================================================

SYSTEM_INSTRUCTION = """
Kamu adalah AI agent yang dapat menggunakan berbagai tools.

Gunakan tools hanya ketika diperlukan untuk menyelesaikan
permintaan pengguna.

ATURAN MEMORY:

1. Gunakan `remember` jika pengguna secara jelas meminta
   agar sebuah informasi disimpan ke memory.

2. Gunakan `recall` jika pengguna ingin mengambil informasi
   berdasarkan key memory tertentu.

3. Gunakan `search_memory` jika pengguna ingin mencari
   informasi yang pernah disimpan dan key-nya belum diketahui
   atau pencarian berdasarkan isi informasi diperlukan.

4. Jangan mengarang isi memory.
   Jika informasi tidak ditemukan, katakan bahwa informasi
   tersebut tidak ditemukan.

5. Jangan menggunakan `remember` hanya karena pengguna
   menyebutkan sebuah informasi. Simpan informasi hanya
   jika pengguna meminta untuk mengingat atau menyimpannya.

6. Untuk operasi yang mengubah data atau filesystem,
   ikuti permission system yang diberikan oleh agent.
"""


# ============================================================
# TOOL DEFINITIONS
# ============================================================

def buat_tool_definitions():

    function_declarations = []

    for nama_tool, data in TOOL_REGISTRY.items():

        declaration = types.FunctionDeclaration(
            name=nama_tool,
            description=data["description"],
            parameters=data["parameters"],
        )

        function_declarations.append(
            declaration
        )

    return [
        types.Tool(
            function_declarations=function_declarations
        )
    ]


# ============================================================
# DETEKSI 429
# ============================================================

def adalah_429(error):
    """
    Mengecek apakah error menunjukkan
    rate limit atau quota exhaustion.
    """

    pesan_error = str(error).lower()

    return (
        "429" in pesan_error
        or "resource_exhausted" in pesan_error
        or "rate limit" in pesan_error
        or "quota" in pesan_error
    )


# ============================================================
# DETEKSI 503
# ============================================================

def adalah_503(error):
    """
    Mengecek apakah server Gemini sedang
    unavailable / high demand.
    """

    pesan_error = str(error).lower()

    return (
        "503" in pesan_error
        or "unavailable" in pesan_error
        or "high demand" in pesan_error
    )


# ============================================================
# REQUEST GEMINI
# ============================================================

def tanya_gemini(pertanyaan, tools):

    """
    Mengirim request ke Gemini.

    Strategi:

    1. Ambil API key aktif dari KeyManager.
    2. Buat client menggunakan key tersebut.
    3. Jika 429:
       tandai key gagal dan pindah ke key berikutnya.
    4. Jika 503:
       retry menggunakan key yang sama.
    5. Jika error lain:
       langsung raise.
    """

    max_percobaan_503 = 3

    percobaan_503 = 0

    while True:

        # ====================================================
        # AMBIL API KEY
        # ====================================================

        key_info = key_manager.get_key()

        nama_key = key_info["name"]
        api_key = key_info["key"]

        print(
            f"\n🔑 Menggunakan {nama_key}"
        )

        # ====================================================
        # BUAT CLIENT
        # ====================================================

        client = genai.Client(
            api_key=api_key
        )

        try:

            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=pertanyaan,
                config=types.GenerateContentConfig(
                    tools=tools,
                    system_instruction=SYSTEM_INSTRUCTION,
                    automatic_function_calling=(
                        types.AutomaticFunctionCallingConfig(
                            disable=True
                        )
                    ),
                ),
            )

            # Request berhasil.

            return response

        except Exception as error:

            # =================================================
            # 429 → PINDAH KEY
            # =================================================

            if adalah_429(error):

                print(
                    f"\n⚠️ {nama_key} terkena "
                    "rate limit / quota."
                )

                key_manager.mark_failed(
                    nama_key
                )

                # Coba key berikutnya.

                try:

                    key_manager.get_key()

                except RuntimeError:

                    print(
                        "\n❌ Semua API key "
                        "sudah tidak tersedia."
                    )

                    raise

                print(
                    "🔄 Beralih ke API key berikutnya..."
                )

                # Reset retry 503 karena kita
                # sudah berpindah key.

                percobaan_503 = 0

                continue

            # =================================================
            # 503 → RETRY
            # =================================================

            if adalah_503(error):

                percobaan_503 += 1

                if (
                    percobaan_503
                    >= max_percobaan_503
                ):

                    print(
                        "\n⚠️ Gemini masih tidak tersedia "
                        "setelah beberapa percobaan."
                    )

                    raise

                waktu_tunggu = (
                    2 ** percobaan_503
                )

                print(
                    f"\n⚠️ Gemini sedang sibuk (503). "
                    f"Percobaan "
                    f"{percobaan_503}/"
                    f"{max_percobaan_503}."
                )

                print(
                    f"⏳ Menunggu "
                    f"{waktu_tunggu} detik..."
                )

                time.sleep(
                    waktu_tunggu
                )

                continue

            # =================================================
            # ERROR LAIN
            # =================================================

            raise
