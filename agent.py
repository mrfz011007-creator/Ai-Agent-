import json

from google.genai import types

from llm import (
    tanya_gemini,
    buat_tool_definitions,
)

from core.runtime import execute_tool

from conversation import ConversationMemory

from local_router import jalankan_lokal


def jalankan_tool(nama_tool, args):
    result = execute_tool(
        nama_tool,
        args,
        source="model",
    )

    payload = {
        "status": result.status,
        "tool": result.tool,
    }

    if result.data is not None:
        payload["hasil"] = result.data

    if result.error:
        payload["pesan"] = result.error

    if result.evidence_id:
        payload["evidence_id"] = result.evidence_id

    return payload


def proses_tool(nama_tool, args):
    return jalankan_tool(nama_tool, args)


def proses_lokal(perintah):
    return jalankan_lokal(perintah)


def main():

    tools = buat_tool_definitions()
    memory = ConversationMemory()

    while True:

        perintah = input("\nAgent > ").strip()

        if not perintah:
            continue

        if perintah.lower() == "exit":
            print("Agent berhenti.")
            break

        hasil_lokal = proses_lokal(perintah)

        if hasil_lokal is not None:

            print("\n⚡ Diproses secara lokal.")
            print(
                "📤 Hasil:",
                json.dumps(
                    hasil_lokal,
                    ensure_ascii=False
                )
            )

            continue

        memory.add_user_message(perintah)

        print("\n🧠 Gemini sedang berpikir...")

        try:

            while True:

                response = tanya_gemini(
                    memory.get_contents(),
                    tools
                )

                kandidat = response.candidates[0]
                model_content = kandidat.content

                memory.add_model_message(model_content)

                function_calls = []

                for part in model_content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)

                if not function_calls:

                    if response.text:
                        print("\nGemini:", response.text)

                    break

                function_response_parts = []

                for call in function_calls:

                    nama_tool = call.name
                    args = dict(call.args)

                    print(f"\n🔧 Tool dipilih: {nama_tool}")
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

            print("\n❌ Terjadi error:")
            print(error)


if __name__ == "__main__":
    main()
