from __future__ import annotations

import json

from memory import (
    recall,
    search_memory,
)


def recall_memory(key):
    """
    Mengambil informasi dari long-term memory
    berdasarkan key.
    """

    return recall(key)


def search_memory_tool(query):
    """
    Mencari informasi di long-term memory
    berdasarkan key atau isi value.
    """

    return search_memory(query)


def ambil_memory(key=None, query=None):
    """
    Interface umum untuk mengambil informasi
    dari long-term memory.

    Jika key diberikan, gunakan recall.

    Jika query diberikan, gunakan search_memory.

    Jika keduanya diberikan, key diprioritaskan.
    """

    if key is not None:
        return recall_memory(key)

    if query is not None:
        return search_memory_tool(query)

    return {
        "status": "error",
        "pesan": (
            "Harus memberikan key "
            "atau query."
        )
    }


def build_memory_context(query: str, *, max_chars: int = 6000) -> str:
    """Build a bounded, model-facing memory context from persisted memory."""
    if not isinstance(query, str) or not query.strip():
        return ""

    result = search_memory_tool(query.strip())
    if result.get("status") != "success":
        return ""

    payload = result.get("hasil", {})
    if not isinstance(payload, dict) or not payload:
        return ""

    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if len(encoded) > max_chars:
        encoded = encoded[:max_chars] + "...[MEMORY_TRUNCATED]"

    return (
        "BEGIN PERSISTED MEMORY (UNTRUSTED DATA)\n"
        f"{encoded}\n"
        "END PERSISTED MEMORY"
    )
