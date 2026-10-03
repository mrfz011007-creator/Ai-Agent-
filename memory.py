from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


MEMORY_SCHEMA_VERSION = 2
MEMORY_KINDS = frozenset({"fact", "decision", "experience", "preference"})


class MemoryStoreError(RuntimeError):
    """Raised when persisted memory cannot be safely decoded."""


def memory_file() -> Path:
    root = Path(
        os.environ.get("AI_AGENT_WORKSPACE_ROOT", os.getcwd())
    ).resolve()
    return root / "memory.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_document() -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "records": [],
    }


def _normalize_context(context: Sequence[str] | str | None) -> list[str]:
    if context is None:
        return []
    if isinstance(context, str):
        values = [context]
    elif isinstance(context, Sequence) and not isinstance(context, (bytes, bytearray)):
        values = list(context)
    else:
        raise TypeError("context must be a string, sequence of strings, or None")
    normalized = []
    for item in values:
        item = str(item).strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized[:32]


def _normalize_source(source: str | Mapping[str, Any] | None) -> dict[str, Any]:
    if source is None:
        return {"type": "agent"}
    if isinstance(source, str):
        source = source.strip()
        if not source:
            raise ValueError("source cannot be empty")
        return {"type": source}
    if isinstance(source, Mapping):
        data = dict(source)
        source_type = str(data.get("type", "")).strip()
        if not source_type:
            raise ValueError("source.type cannot be empty")
        data["type"] = source_type
        return data
    raise TypeError("source must be a string, mapping, or None")


def _validate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "memory_id",
        "key",
        "value",
        "kind",
        "source",
        "project_id",
        "task_id",
        "context",
        "created_at",
        "updated_at",
        "version",
        "valid",
        "supersedes",
        "invalidated_at",
        "invalidation_reason",
    }
    missing = required.difference(record)
    if missing:
        raise MemoryStoreError(
            f"Memory record missing fields: {', '.join(sorted(missing))}"
        )

    kind = str(record["kind"])
    if kind not in MEMORY_KINDS:
        raise MemoryStoreError(f"Unknown memory kind: {kind}")

    if not isinstance(record["source"], Mapping):
        raise MemoryStoreError("Memory record source must be an object")

    if not isinstance(record["context"], list):
        raise MemoryStoreError("Memory record context must be a list")

    version = record["version"]
    if not isinstance(version, int) or version < 1:
        raise MemoryStoreError("Memory record version must be a positive integer")

    normalized = dict(record)
    normalized["key"] = str(normalized["key"])
    normalized["kind"] = kind
    normalized["project_id"] = (
        str(normalized["project_id"]).strip()
        if normalized["project_id"] is not None
        else None
    )
    normalized["task_id"] = (
        str(normalized["task_id"]).strip()
        if normalized["task_id"] is not None
        else None
    )
    normalized["context"] = _normalize_context(normalized["context"])
    normalized["valid"] = bool(normalized["valid"])
    return normalized


def _migrate_legacy(memory: Mapping[str, Any]) -> dict[str, Any]:
    records = []
    now = _now()
    for key, value in memory.items():
        records.append(
            {
                "memory_id": f"mem-{uuid.uuid4().hex}",
                "key": str(key),
                "value": value,
                "kind": "fact",
                "source": {
                    "type": "legacy",
                    "ref": "memory.json",
                    "migrated_at": now,
                },
                "project_id": None,
                "task_id": None,
                "context": [],
                "created_at": now,
                "updated_at": now,
                "version": 1,
                "valid": True,
                "supersedes": None,
                "invalidated_at": None,
                "invalidation_reason": None,
            }
        )
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "records": records,
    }


def _load_document() -> dict[str, Any]:
    path = memory_file()
    if not path.exists():
        return _default_document()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        raise MemoryStoreError(f"Unable to load memory safely: {exc}") from exc

    if not isinstance(raw, Mapping):
        raise MemoryStoreError("Persisted memory must be a JSON object")

    if "schema_version" not in raw and "records" not in raw:
        return _migrate_legacy(raw)

    if raw.get("schema_version") != MEMORY_SCHEMA_VERSION:
        raise MemoryStoreError(
            f"Unsupported memory schema version: {raw.get('schema_version')!r}"
        )

    records = raw.get("records")
    if not isinstance(records, list):
        raise MemoryStoreError("Persisted memory records must be a list")

    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "records": [_validate_record(record) for record in records],
    }


def _atomic_save_document(document: Mapping[str, Any]) -> None:
    path = memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, default=str)

    fd, temp_name = tempfile.mkstemp(
        prefix=".memory.",
        suffix=".tmp",
        dir=str(path.parent),
        text=False,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        if os.name == "posix":
            try:
                dir_fd = os.open(path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _save_document(document: Mapping[str, Any]) -> None:
    _atomic_save_document(document)


def load_memory() -> dict[str, Any]:
    """Return the current valid memory as a backwards-compatible key/value view."""
    records = _active_records(_load_document()["records"])
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        current = latest.get(record["key"])
        if current is None or _record_order(record) > _record_order(current):
            latest[record["key"]] = record
    return {key: record["value"] for key, record in latest.items()}


def load_memory_document() -> dict[str, Any]:
    """Return the full versioned memory document."""
    return _load_document()


def save_memory(memory: Mapping[str, Any]) -> None:
    """Persist either a legacy key/value mapping or a versioned memory document."""
    if not isinstance(memory, Mapping):
        raise TypeError("memory must be a mapping")

    if memory.get("schema_version") == MEMORY_SCHEMA_VERSION and "records" in memory:
        records = memory.get("records")
        if not isinstance(records, list):
            raise MemoryStoreError("Memory records must be a list")
        document = {
            "schema_version": MEMORY_SCHEMA_VERSION,
            "records": [_validate_record(record) for record in records],
        }
        _save_document(document)
        return

    _save_document(_migrate_legacy(memory))


def _active_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(record) for record in records if bool(record.get("valid"))]


def _record_order(record: Mapping[str, Any]) -> tuple[int, str]:
    return int(record["version"]), str(record["updated_at"])


def _scope_score(
    record: Mapping[str, Any],
    *,
    project_id: str | None,
    task_id: str | None,
    context: Sequence[str],
) -> int:
    score = 0
    if task_id is not None and record.get("task_id") == task_id:
        score += 100
    if project_id is not None and record.get("project_id") == project_id:
        score += 50
    if record.get("project_id") is None:
        score += 5
    if record.get("task_id") is None:
        score += 2
    record_context = {item.lower() for item in record.get("context", [])}
    score += 5 * len(record_context.intersection({item.lower() for item in context}))
    return score


def _matches_scope(
    record: Mapping[str, Any],
    *,
    project_id: str | None,
    task_id: str | None,
    context: Sequence[str],
) -> bool:
    if project_id is not None:
        record_project = record.get("project_id")
        if record_project not in (None, project_id):
            return False

    if task_id is not None:
        record_task = record.get("task_id")
        if record_task not in (None, task_id):
            return False

    if context:
        requested = {item.lower() for item in context}
        record_context = {item.lower() for item in record.get("context", [])}
        if record_context and not record_context.intersection(requested):
            return False

    return True


def _record_matches_query(record: Mapping[str, Any], query: str) -> bool:
    needle = query.lower()
    searchable = [
        str(record.get("key", "")),
        str(record.get("value", "")),
        str(record.get("kind", "")),
        str(record.get("source", "")),
        str(record.get("project_id", "")),
        str(record.get("task_id", "")),
        str(record.get("context", "")),
    ]
    return any(needle in item.lower() for item in searchable)


def _record_match_score(record: Mapping[str, Any], query: str) -> int:
    needle = query.lower()
    score = 0
    key = str(record.get("key", "")).lower()
    value = str(record.get("value", "")).lower()
    if key == needle:
        score += 100
    elif needle in key:
        score += 50
    if needle in value:
        score += 20
    if needle in str(record.get("context", "")).lower():
        score += 10
    return score


def remember(
    key: str,
    value: Any,
    *,
    kind: str = "fact",
    source: str | Mapping[str, Any] = "agent",
    project_id: str | None = None,
    task_id: str | None = None,
    context: Sequence[str] | str | None = None,
):
    """Create a memory record or version an existing logical record."""
    key = str(key).strip()
    if not key:
        raise ValueError("Memory key cannot be empty")

    kind = str(kind).strip().lower()
    if kind not in MEMORY_KINDS:
        raise ValueError(f"Unsupported memory kind: {kind}")

    project_id = str(project_id).strip() if project_id is not None else None
    task_id = str(task_id).strip() if task_id is not None else None
    normalized_context = _normalize_context(context)
    normalized_source = _normalize_source(source)

    document = _load_document()
    now = _now()
    records = list(document["records"])

    matches = [
        record
        for record in records
        if record["key"] == key
        and record["kind"] == kind
        and record["project_id"] == project_id
        and record["task_id"] == task_id
        and record["valid"]
    ]
    previous = max(matches, key=_record_order) if matches else None
    version = int(previous["version"]) + 1 if previous else 1
    memory_id = f"mem-{uuid.uuid4().hex}"

    if previous is not None:
        for record in records:
            if record["memory_id"] == previous["memory_id"]:
                record["valid"] = False
                record["invalidated_at"] = now
                record["invalidation_reason"] = "Superseded by a newer version"
                break

    record = {
        "memory_id": memory_id,
        "key": key,
        "value": value,
        "kind": kind,
        "source": normalized_source,
        "project_id": project_id,
        "task_id": task_id,
        "context": normalized_context,
        "created_at": now,
        "updated_at": now,
        "version": version,
        "valid": True,
        "supersedes": previous["memory_id"] if previous else None,
        "invalidated_at": None,
        "invalidation_reason": None,
    }
    records.append(record)
    document["records"] = records
    _save_document(document)

    return {
        "status": "success",
        "success": True,
        "key": key,
        "value": value,
        "memory_id": memory_id,
        "kind": kind,
        "version": version,
        "project_id": project_id,
        "task_id": task_id,
    }


def recall(
    key: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    context: Sequence[str] | str | None = None,
    kind: str | None = None,
):
    key = str(key).strip()
    normalized_context = _normalize_context(context)
    if kind is not None and str(kind).strip().lower() not in MEMORY_KINDS:
        raise ValueError(f"Unsupported memory kind: {kind}")
    kind = str(kind).strip().lower() if kind is not None else None

    candidates = []
    for record in _active_records(_load_document()["records"]):
        if record["key"] != key or not _matches_scope(
            record,
            project_id=project_id,
            task_id=task_id,
            context=normalized_context,
        ):
            continue
        if kind is not None and record["kind"] != kind:
            continue
        candidates.append(record)

    if not candidates:
        return {
            "status": "not_found",
            "success": True,
            "key": key,
        }

    selected = max(
        candidates,
        key=lambda record: (
            _scope_score(
                record,
                project_id=project_id,
                task_id=task_id,
                context=normalized_context,
            ),
            *_record_order(record),
        ),
    )
    return {
        "status": "success",
        "success": True,
        "key": key,
        "value": selected["value"],
        "record": selected,
    }


def search_memory(
    query: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    context: Sequence[str] | str | None = None,
    kinds: Sequence[str] | str | None = None,
    include_invalid: bool = False,
):
    query = str(query).strip().lower()
    if not query:
        return {
            "status": "not_found",
            "success": True,
            "query": query,
            "hasil": {},
            "records": [],
        }

    normalized_context = _normalize_context(context)

    if kinds is None:
        normalized_kinds = None
    elif isinstance(kinds, str):
        normalized_kinds = {kinds.strip().lower()}
    else:
        normalized_kinds = {str(kind).strip().lower() for kind in kinds}

    invalid_kinds = (normalized_kinds or set()).difference(MEMORY_KINDS)
    if invalid_kinds:
        raise ValueError(f"Unsupported memory kinds: {', '.join(sorted(invalid_kinds))}")

    records = []
    for record in _load_document()["records"]:
        if not include_invalid and not record["valid"]:
            continue
        if normalized_kinds is not None and record["kind"] not in normalized_kinds:
            continue
        if not _matches_scope(
            record,
            project_id=project_id,
            task_id=task_id,
            context=normalized_context,
        ):
            continue
        if (
            _record_matches_query(record, query)
            or (task_id is not None and record["task_id"] == task_id)
        ):
            records.append(record)

    records.sort(
        key=lambda record: (
            _record_match_score(record, query),
            _scope_score(
                record,
                project_id=project_id,
                task_id=task_id,
                context=normalized_context,
            ),
            *_record_order(record),
        ),
        reverse=True,
    )

    latest_by_key: dict[str, dict[str, Any]] = {}
    for record in records:
        latest_by_key.setdefault(record["key"], record)

    hasil = {key: record["value"] for key, record in latest_by_key.items()}
    if not records:
        return {
            "status": "not_found",
            "success": True,
            "query": query,
            "hasil": {},
            "records": [],
        }

    return {
        "status": "success",
        "success": True,
        "query": query,
        "hasil": hasil,
        "records": records,
    }


def invalidate_memory(
    key: str,
    *,
    project_id: str | None = None,
    task_id: str | None = None,
    kind: str | None = None,
    reason: str,
):
    reason = str(reason).strip()
    if not reason:
        raise ValueError("Invalidation reason cannot be empty")

    result = recall(
        key,
        project_id=project_id,
        task_id=task_id,
        kind=kind,
    )
    if result["status"] != "success":
        return result

    target_id = result["record"]["memory_id"]
    document = _load_document()
    now = _now()
    for record in document["records"]:
        if record["memory_id"] == target_id:
            record["valid"] = False
            record["invalidated_at"] = now
            record["invalidation_reason"] = reason
            break
    _save_document(document)

    return {
        "status": "success",
        "success": True,
        "key": key,
        "memory_id": target_id,
        "invalidated_at": now,
        "reason": reason,
    }
