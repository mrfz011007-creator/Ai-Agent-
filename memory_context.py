from __future__ import annotations

import json
from typing import Sequence

from memory import (
    recall,
    search_memory,
)
from security.redaction import redact_value


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


def _encode_records(records) -> str:
    return json.dumps(
        {"records": records},
        ensure_ascii=False,
        indent=2,
        default=str,
    )


def _fit_single_record(record, limit: int):
    value = str(record.get("value", ""))
    candidate = {
        "memory_id": record.get("memory_id"),
        "key": record.get("key"),
        "value": "",
        "kind": record.get("kind"),
        "source": record.get("source"),
        "project_id": record.get("project_id"),
        "task_id": record.get("task_id"),
        "version": record.get("version"),
        "valid": record.get("valid"),
    }

    if len(_encode_records([candidate])) > limit:
        candidate = {
            "memory_id": record.get("memory_id"),
            "key": record.get("key"),
            "value": "",
            "kind": record.get("kind"),
            "project_id": record.get("project_id"),
            "task_id": record.get("task_id"),
            "version": record.get("version"),
        }

    if len(_encode_records([candidate])) > limit:
        return None

    low, high = 0, len(value)
    best = _encode_records([candidate])
    while low <= high:
        mid = (low + high) // 2
        candidate["value"] = value[:mid]
        encoded = _encode_records([candidate])
        if len(encoded) <= limit:
            best = encoded
            low = mid + 1
        else:
            high = mid - 1

    return best


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
    records = redact_value(records)

    selected = []
    for record in records:
        candidate = selected + [record]
        encoded = _encode_records(candidate)
        if len(encoded) > payload_limit:
            break
        selected = candidate

    if selected:
        encoded = _encode_records(selected)
    else:
        encoded = _fit_single_record(records[0], payload_limit)
        if encoded is None:
            return prefix + "...[MEMORY_TOO_LARGE]" + suffix

    return prefix + encoded + suffix
