import json

from google.genai import types

from llm import (
    tanya_gemini,
    buat_tool_definitions,
)

from registry import (
    get_tool_function,
    get_tool_permission,
)

from permissions import minta_konfirmasi

from conversation import ConversationMemory

from local_router import jalankan_lokal


def jalankan_tool(nama_tool, args):

    tool = get_tool_function(
        nama_tool
    )

    if tool is None:

        return {
            "status": "error",
            "pesan": f"Tool tidak ditemukan: {nama_tool}"
        }

    try:

        hasil = tool(**args)

        return {
            "status": "success",
            "tool": nama_tool,
            "hasil": hasil
        }

    except Exception as error:

        return {
            "status": "error",
            "tool": nama_tool,
            "pesan": str(error)
        }


def proses_tool(nama_tool, args):

    permission = get_tool_permission(
        nama_tool
    )

    if permission == "blocked":

        print(
            "⛔ Tool diblokir."
        )

        return {
            "status": "error",
            "pesan": (
                f"Tool '{nama_tool}' "
                "diblokir oleh permission system."
            )
        }

    if permission == "confirm":

        if minta_konfirmasi(
            nama_tool,
            args
        ):

            print(
                "✅ Konfirmasi diterima."
            )

            return jalankan_tool(
                nama_tool,
                args
            )

        print(
            "❌ Tool dibatalkan oleh pengguna."
        )

        return {
            "status": "cancelled",
            "pesan": (
                "Pengguna menolak "
                "menjalankan tool."
            )
        }

    if permission == "safe":

        print(
            "🟢 Tool aman, "
            "dijalankan otomatis."
        )

        return jalankan_tool(
            nama_tool,
            args
        )

    return {
        "status": "error",
        "pesan": "Permission tidak dikenal."
    }


def proses_lokal(perintah):

    """
    Mencoba memproses perintah menggunakan
    Local Router.

    Jika perintah dapat ditangani secara lokal,
    hasil dikembalikan.

    Jika tidak dapat ditangani lokal,
    return None agar Agent dapat meneruskannya
    ke Gemini.
    """

    hasil = jalankan_lokal(
        perintah
    )

    if hasil is None:

        return None

    return hasil


def main():

    tools = buat_tool_definitions()

    memory = ConversationMemory()

    while True:

        perintah = input(
            "\nAgent > "
        ).strip()

        if not perintah:
            continue

        if perintah.lower() == "exit":

            print(
                "Agent berhenti."
            )

            break

        # ==================================================
        # LOCAL-FIRST
        # ==================================================

        hasil_lokal = proses_lokal(
            perintah
        )

        if hasil_lokal is not None:

            print(
                "\n⚡ Diproses secara lokal."
            )

            print(
                "📤 Hasil:",
                json.dumps(
                    hasil_lokal,
                    ensure_ascii=False
                )
            )

            continue

        # ==================================================
        # GEMINI
        # ==================================================

        memory.add_user_message(
            perintah
        )

        print(
            "\n🧠 Gemini sedang berpikir..."
        )

        try:

            while True:

                response = tanya_gemini(
                    memory.get_contents(),
                    tools
                )

                kandidat = response.candidates[0]
                model_content = kandidat.content

                memory.add_model_message(
                    model_content
                )

                function_calls = []

                for part in model_content.parts:

                    if part.function_call:

                        function_calls.append(
                            part.function_call
                        )

                if not function_calls:

                    if response.text:

                        print(
                            "\nGemini:",
                            response.text
                        )

                    break

                function_response_parts = []

                for call in function_calls:

                    nama_tool = call.name
                    args = dict(call.args)

                    print(
                        f"\n🔧 Tool dipilih: "
                        f"{nama_tool}"
                    )

                    print(
                        "📦 Argumen:",
                        json.dumps(
                            args,
                            ensure_ascii=False
                        )
                    )

                    hasil = proses_tool(
                        nama_tool,
                        args
                    )

                    print(
                        "📤 Hasil:",
                        json.dumps(
                            hasil,
                            ensure_ascii=False
                        )
                    )

                    function_response_parts.append(
                        types.Part.from_function_response(
                            name=nama_tool,
                            response=hasil
                        )
                    )

                memory.add_tool_response(
                    function_response_parts
                )

        except Exception as error:

            print(
                "\n❌ Terjadi error:"
            )

            print(error)


if __name__ == "__main__":
    main()
