from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

MEMORY_SCHEMA_VERSION = 2
MEMORY_TYPES = frozenset({"fact", "decision", "experience", "preference"})
MEMORY_STATUSES = frozenset({"active", "invalidated", "superseded", "archived"})
MEMORY_RETENTION_POLICIES = frozenset({"normal", "durable", "ephemeral"})


class MemoryValidationError(ValueError):
    """Raised when a memory record or store violates the memory schema."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root() -> Path:
    return Path(os.environ.get("AI_AGENT_WORKSPACE_ROOT", os.getcwd())).resolve()


def memory_file() -> Path:
    return _root() / "memory.json"


def _tokenize(value: Any) -> set[str]:
    # Split compound keys/paths on punctuation so a query for "task inspect
    # execution" can retrieve a key such as "task:inspect:execution".
    return {
        token
        for token in re.findall(r"[a-zA-Z0-9_]+", str(value).lower())
        if len(token) >= 2
    }


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise MemoryValidationError("MEMORY_VALUE_NOT_JSON_SERIALIZABLE") from error
    return value


def _normalize_source(source: Any) -> dict[str, Any]:
    if source is None:
        return {"kind": "system", "ref": "unknown"}
    if isinstance(source, str):
        if not source.strip():
            raise MemoryValidationError("MEMORY_SOURCE_EMPTY")
        return {"kind": "external", "ref": source}
    if isinstance(source, Mapping):
        result = dict(source)
        if not result.get("kind") or not result.get("ref"):
            raise MemoryValidationError("MEMORY_SOURCE_REQUIRES_KIND_AND_REF")
        return result
    raise MemoryValidationError("MEMORY_SOURCE_INVALID")


def _normalize_context(context: Any) -> dict[str, Any]:
    if context is None:
        return {}
    if not isinstance(context, Mapping):
        raise MemoryValidationError("MEMORY_CONTEXT_INVALID")
    return dict(context)


def _validate_record(record: Mapping[str, Any]) -> None:
    required = {
        "id", "type", "key", "value", "project_id", "task_id", "context",
        "source", "provenance", "created_at", "updated_at", "version",
        "status", "invalidated_at", "invalidated_by", "supersedes_id", "tags",
        "importance", "confidence", "retention", "summary", "evidence_refs", "last_accessed_at",
    }
    missing = required - set(record)
    if missing:
        raise MemoryValidationError(
            "MEMORY_RECORD_MISSING_FIELDS:" + ",".join(sorted(missing))
        )
    if record["type"] not in MEMORY_TYPES:
        raise MemoryValidationError("MEMORY_TYPE_INVALID")
    if not isinstance(record["key"], str) or not record["key"].strip():
        raise MemoryValidationError("MEMORY_KEY_INVALID")
    if not isinstance(record["version"], int) or record["version"] < 1:
        raise MemoryValidationError("MEMORY_VERSION_INVALID")
    if record["status"] not in MEMORY_STATUSES:
        raise MemoryValidationError("MEMORY_STATUS_INVALID")
    if not isinstance(record["context"], Mapping):
        raise MemoryValidationError("MEMORY_CONTEXT_INVALID")
    if not isinstance(record["source"], Mapping):
        raise MemoryValidationError("MEMORY_SOURCE_INVALID")
    if not isinstance(record["provenance"], Mapping):
        raise MemoryValidationError("MEMORY_PROVENANCE_INVALID")
    if not isinstance(record["tags"], list) or not all(
        isinstance(tag, str) for tag in record["tags"]
    ):
        raise MemoryValidationError("MEMORY_TAGS_INVALID")
    _json_safe(record["value"])


def _empty_store() -> dict[str, Any]:
    return {"schema_version": MEMORY_SCHEMA_VERSION, "records": []}


def _migrate_legacy(value: Mapping[str, Any]) -> dict[str, Any]:
    records = []
    now = _utc_now()
    for key, item in value.items():
        records.append(
            _new_record(
                memory_type="fact",
                key=str(key),
                value=item,
                project_id=None,
                task_id=None,
                context={},
                source={"kind": "migration", "ref": "legacy-memory-json"},
                provenance={"reason": "migrated from legacy flat key/value memory"},
                tags=["migrated"],
                now=now,
            )
        )
    return {"schema_version": MEMORY_SCHEMA_VERSION, "records": records}


def _load_store() -> dict[str, Any]:
    path = memory_file()
    if not path.exists():
        return _empty_store()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError("MEMORY_CORRUPTED_OR_UNREADABLE") from error
    if not isinstance(value, Mapping):
        raise RuntimeError("MEMORY_FORMAT_INVALID")

    if "records" not in value:
        store = _migrate_legacy(value)
    else:
        if value.get("schema_version") != MEMORY_SCHEMA_VERSION:
            raise RuntimeError("MEMORY_SCHEMA_VERSION_UNSUPPORTED")
        records = value.get("records")
        if not isinstance(records, list):
            raise RuntimeError("MEMORY_RECORDS_INVALID")
        store = {"schema_version": MEMORY_SCHEMA_VERSION, "records": [dict(r) for r in records]}
        if schema_version == 1:
            for record in store["records"]:
                record.setdefault("importance", 0.5)
                record.setdefault("confidence", 0.5)
                record.setdefault("retention", "normal")
                record.setdefault("summary", str(record.get("value", ""))[:500])
                record.setdefault("evidence_refs", [])
                record.setdefault("last_accessed_at", None)

    for record in store["records"]:
        _validate_record(record)
    return store


def load_memory() -> dict[str, Any]:
    return _load_store()


def save_memory(memory: Mapping[str, Any]) -> None:
    if not isinstance(memory, Mapping):
        raise TypeError("memory must be an object")
    if memory.get("schema_version") != MEMORY_SCHEMA_VERSION:
        raise MemoryValidationError("MEMORY_SCHEMA_VERSION_INVALID")
    records = memory.get("records")
    if not isinstance(records, list):
        raise MemoryValidationError("MEMORY_RECORDS_INVALID")
    for record in records:
        _validate_record(record)

    path = memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(memory, ensure_ascii=False, indent=2, sort_keys=True)
    fd, temporary = tempfile.mkstemp(prefix=".memory-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _new_record(
    *,
    memory_type: str,
    key: str,
    value: Any,
    project_id: str | None,
    task_id: str | None,
    context: Mapping[str, Any],
    source: Any,
    provenance: Mapping[str, Any],
    tags: list[str],
    version: int = 1,
    supersedes_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    if memory_type not in MEMORY_TYPES:
        raise MemoryValidationError("MEMORY_TYPE_INVALID")
    if not isinstance(key, str) or not key.strip():
        raise MemoryValidationError("MEMORY_KEY_INVALID")
    if not isinstance(provenance, Mapping):
        raise MemoryValidationError("MEMORY_PROVENANCE_INVALID")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise MemoryValidationError("MEMORY_TAGS_INVALID")

    timestamp = now or _utc_now()
    record = {
        "id": str(uuid.uuid4()),
        "type": memory_type,
        "key": key.strip(),
        "value": _json_safe(value),
        "project_id": project_id,
        "task_id": task_id,
        "context": _normalize_context(context),
        "source": _normalize_source(source),
        "provenance": dict(provenance),
        "created_at": timestamp,
        "updated_at": timestamp,
        "version": version,
        "status": "active",
        "invalidated_at": None,
        "invalidated_by": None,
        "supersedes_id": supersedes_id,
        "tags": sorted(set(tags)),
    }
    _validate_record(record)
    return record


def _scope_matches(
    record: Mapping[str, Any],
    *,
    key: str | None = None,
    memory_type: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
) -> bool:
    return (
        (key is None or record["key"] == key)
        and (memory_type is None or record["type"] == memory_type)
        and (project_id is None or record["project_id"] == project_id)
        and (task_id is None or record["task_id"] == task_id)
    )


def _active_matches(store: Mapping[str, Any], **filters: Any) -> list[dict[str, Any]]:
    return [
        record for record in store["records"]
        if record["status"] == "active" and _scope_matches(record, **filters)
    ]


def remember(
    key: str,
    value: Any,
    *,
    memory_type: str = "fact",
    project_id: str | None = None,
    task_id: str | None = None,
    context: Mapping[str, Any] | None = None,
    source: Any = None,
    provenance: Mapping[str, Any] | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    store = _load_store()
    previous = _active_matches(
        store,
        key=key,
        memory_type=memory_type,
        project_id=project_id,
        task_id=task_id,
    )
    latest_version = max((record["version"] for record in previous), default=0)
    supersedes_id = None
    now = _utc_now()

    for record in previous:
        record["status"] = "superseded"
        record["invalidated_at"] = now
        record["invalidated_by"] = "revision"
        record["updated_at"] = now
        supersedes_id = record["id"]

    record = _new_record(
        memory_type=memory_type,
        key=key,
        value=value,
        project_id=project_id,
        task_id=task_id,
        context=context or {},
        source=source,
        provenance=provenance or {"reason": "memory_write"},
        tags=tags or [],
        version=latest_version + 1,
        supersedes_id=supersedes_id,
        now=now,
    )
    store["records"].append(record)
    save_memory(store)
    return {"status": "success", "success": True, "record": record}


def recall(
    key: str,
    *,
    memory_type: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    include_invalidated: bool = False,
) -> dict[str, Any]:
    store = _load_store()
    records = [
        record for record in store["records"]
        if _scope_matches(
            record,
            key=key,
            memory_type=memory_type,
            project_id=project_id,
            task_id=task_id,
        )
        and (include_invalidated or record["status"] == "active")
    ]
    records.sort(key=lambda record: (record["version"], record["updated_at"]), reverse=True)
    if not records:
        return {"status": "not_found", "success": True, "key": key}
    record = records[0]
    return {
        "status": "success",
        "success": True,
        "key": key,
        "value": record["value"],
        "record": record,
    }


def invalidate_memory(record_id: str, *, reason: str = "invalidated") -> dict[str, Any]:
    store = _load_store()
    for record in store["records"]:
        if record["id"] == record_id:
            if record["status"] == "invalidated":
                return {"status": "already_invalidated", "success": True, "record": record}
            now = _utc_now()
            record["status"] = "invalidated"
            record["invalidated_at"] = now
            record["invalidated_by"] = reason
            record["updated_at"] = now
            save_memory(store)
            return {"status": "success", "success": True, "record": record}
    return {"status": "not_found", "success": True, "record_id": record_id}


def update_memory(
    record_id: str,
    value: Any,
    *,
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    store = _load_store()
    for record in store["records"]:
        if record["id"] == record_id:
            if record["status"] != "active":
                return {"status": "invalidated", "success": False, "record": record}
            return remember(
                record["key"],
                value,
                memory_type=record["type"],
                project_id=record["project_id"],
                task_id=record["task_id"],
                context=record["context"],
                source=record["source"],
                provenance=provenance or {"reason": "memory_update", "previous_id": record_id},
                tags=record["tags"],
            )
    return {"status": "not_found", "success": True, "record_id": record_id}


def _record_text(record: Mapping[str, Any]) -> str:
    return " ".join([
        record["key"],
        str(record["value"]),
        record["type"],
        record["project_id"] or "",
        record["task_id"] or "",
        str(record["context"]),
        str(record["source"]),
        str(record["tags"]),
    ]).lower()


def _score_record(
    record: Mapping[str, Any],
    query_tokens: set[str],
    *,
    project_id: str | None,
    task_id: str | None,
    context: Mapping[str, Any] | None,
) -> float:
    score = 0.0
    text = _record_text(record)
    for token in query_tokens:
        if token in record["key"].lower():
            score += 5.0
        elif token in text:
            score += 1.0
    if project_id is not None:
        score += 4.0 if record["project_id"] == project_id else -2.0
    if task_id is not None:
        score += 5.0 if record["task_id"] == task_id else -1.0
    if context:
        for key, value in context.items():
            if record["context"].get(key) == value:
                score += 2.0
    score += min(record["version"], 10) * 0.05
    score += float(record.get("importance", 0.5)) * 2.0
    score += float(record.get("confidence", 0.5))
    return score


def search_memory(
    query: str = "",
    *,
    memory_type: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
    context: Mapping[str, Any] | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
    include_invalidated: bool = False,
) -> dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if memory_type is not None and memory_type not in MEMORY_TYPES:
        raise MemoryValidationError("MEMORY_TYPE_INVALID")

    store = _load_store()
    query_tokens = _tokenize(query)
    wanted_tags = set(tags or [])
    candidates = []

    for record in store["records"]:
        if not include_invalidated and record["status"] != "active":
            continue
        if memory_type and record["type"] != memory_type:
            continue
        if project_id is not None and record["project_id"] != project_id:
            continue
        if task_id is not None and record["task_id"] != task_id:
            continue
        if wanted_tags and not wanted_tags.intersection(record["tags"]):
            continue

        score = _score_record(
            record,
            query_tokens,
            project_id=project_id,
            task_id=task_id,
            context=context,
        )
        if query_tokens and score <= 0:
            continue
        candidates.append((score, record))

    candidates.sort(
        key=lambda item: (item[0], item[1]["updated_at"], item[1]["version"]),
        reverse=True,
    )
    results = [
        {"score": round(score, 3), "record": record}
        for score, record in candidates[:limit]
    ]
    return {
        "status": "success" if results else "not_found",
        "success": True,
        "query": query,
        "results": results,
        "hasil": {item["record"]["key"]: item["record"]["value"] for item in results},
    }


def get_memory_history(
    key: str,
    *,
    memory_type: str | None = None,
    project_id: str | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    store = _load_store()
    records = [
        record for record in store["records"]
        if _scope_matches(
            record,
            key=key,
            memory_type=memory_type,
            project_id=project_id,
            task_id=task_id,
        )
    ]
    return sorted(records, key=lambda record: record["version"], reverse=True)
