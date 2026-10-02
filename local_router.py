from intent_router import deteksi_intent
from core.runtime import execute_tool


def jalankan_tool_lokal(nama_tool, args):
    result = execute_tool(
        nama_tool,
        args,
        source="local_router",
    )

    payload = {
        "status": "local_" + result.status,
        "tool": result.tool,
    }

    if result.data is not None:
        payload["hasil"] = result.data

    if result.error:
        payload["pesan"] = result.error

    if result.evidence_id:
        payload["evidence_id"] = result.evidence_id

    return payload


def jalankan_lokal(perintah):
    hasil_intent = deteksi_intent(perintah)

    if hasil_intent is None:
        return None

    nama_tool = hasil_intent["tool"]
    args = {}

    if hasil_intent["jenis"] == "memory":
        args = {"key": hasil_intent["key"]}

    return jalankan_tool_lokal(
        nama_tool,
        args
    )
