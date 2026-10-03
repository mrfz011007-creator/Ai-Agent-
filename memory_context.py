from __future__ import annotations

import json
from typing import Sequence

from memory import (
    recall,
    search_memory,
)


def recall_memory(
    key,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    context: Sequence[str] | str | None = None,
    kind: str | None = None,
):
    """
    Mengambil informasi dari long-term memory berdasarkan key dan scope.
    """

    return recall(
        key,
        project_id=project_id,
        task_id=task_id,
        context=context,
        kind=kind,
    )


def search_memory_tool(
    query,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    context: Sequence[str] | str | None = None,
    kinds: Sequence[str] | str | None = None,
    include_invalid: bool = False,
):
    """
    Mencari informasi di long-term memory berdasarkan isi dan scope.
    """

    return search_memory(
        query,
        project_id=project_id,
        task_id=task_id,
        context=context,
        kinds=kinds,
        include_invalid=include_invalid,
    )


def ambil_memory(
    key=None,
    query=None,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    context: Sequence[str] | str | None = None,
    kind: str | None = None,
):
    """
    Interface umum untuk mengambil informasi dari long-term memory.

    Jika key diberikan, gunakan recall.
    Jika query diberikan, gunakan search_memory.
    Jika keduanya diberikan, key diprioritaskan.
    """

    if key is not None:
        return recall_memory(
            key,
            project_id=project_id,
            task_id=task_id,
            context=context,
            kind=kind,
        )

    if query is not None:
        return search_memory_tool(
            query,
            project_id=project_id,
            task_id=task_id,
            context=context,
        )

    return {
        "status": "error",
        "pesan": "Harus memberikan key atau query.",
    }


def build_memory_context(
    query: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    context: Sequence[str] | str | None = None,
    kinds: Sequence[str] | str | None = None,
    max_chars: int = 6000,
) -> str:
    """Build a bounded, scoped, model-facing memory context."""
    if not isinstance(query, str) or not query.strip():
        return ""
    if not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")

    prefix = "BEGIN PERSISTED MEMORY (UNTRUSTED DATA)\n"
    suffix = "\nEND PERSISTED MEMORY"
    payload_limit = max_chars - len(prefix) - len(suffix)
    if payload_limit <= 0:
        return (prefix + suffix)[:max_chars]

    result = search_memory_tool(
        query.strip(),
        project_id=project_id,
        task_id=task_id,
        context=context,
        kinds=kinds,
    )
    if result.get("status") != "success":
        return ""

    records = result.get("records", [])
    if not isinstance(records, list) or not records:
        return ""

    selected = []
    for record in records:
        candidate = selected + [record]
        encoded = json.dumps(
            {"records": candidate},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        if len(encoded) > payload_limit:
            break
        selected = candidate

    if not selected:
        first = dict(records[0])
        value = str(first.get("value", ""))
        remaining = max(1, payload_limit // 4)
        first["value"] = value[:remaining]
        selected = [first]

    encoded = json.dumps(
        {"records": selected},
        ensure_ascii=False,
        indent=2,
        default=str,
    )
    if len(encoded) > payload_limit:
        marker = "...[MEMORY_TRUNCATED]"
        if payload_limit <= len(marker):
            encoded = marker[:payload_limit]
        else:
            encoded = (
                encoded[: payload_limit - len(marker)]
                + marker
            )

    return prefix + encoded + suffix
