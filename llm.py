import os
from google import genai
from google.genai import types

from registry import TOOL_REGISTRY

from core.model_gateway import ModelGateway, gemini_credentials


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
# REQUEST GEMINI
# ============================================================

_model_gateway = ModelGateway(
    credentials=gemini_credentials(),
    client_factory=lambda api_key: genai.Client(api_key=api_key),
)


def tanya_gemini(pertanyaan, tools):
    """Send one bounded model request through the provider gateway."""

    def invoke(client):
        return client.models.generate_content(
            model="gemini-3.8-flash",
            contents=pertanyaan,
            config=types.GenerateContentConfig(
                tools=tools,
                system_instruction=SYSTEM_INSTRUCTION,
                automatic_function_calling=(
                    types.AutomaticFunctionCallingConfig(disable=True)
                ),
            ),
        )

    return _model_gateway.call(invoke)
