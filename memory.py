from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


MEMORY_SCHEMA_VERSION = 3
LEGACY_VERSIONED_SCHEMA_VERSION = 2
MEMORY_KINDS = frozenset({"fact", "decision", "experience", "preference"})
MEMORY_MAX_KEY_CHARS = 256
MEMORY_MAX_VALUE_JSON_CHARS = 12000
MEMORY_MAX_SOURCE_JSON_CHARS = 2048


class MemoryStoreError(RuntimeError):
    """Raised when persisted memory cannot be safely decoded."""


@contextmanager
def _memory_write_lock():
    """Serialize memory read-modify-write operations across processes."""
    path = memory_file()
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as stream:
        if os.name != "posix":
            yield
            return

        import fcntl

        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


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
            source_type = str(data.get("kind", "")).strip()
        if not source_type:
            raise ValueError("source.type cannot be empty")
        data["type"] = source_type
        return data
    raise TypeError("source must be a string, mapping, or None")


_MEMORY_REQUIRED_FIELDS = frozenset({
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
})


def _validate_record(record: Mapping[str, Any]) -> dict[str, Any]:
    required = _MEMORY_REQUIRED_FIELDS
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
    normalized["memory_id"] = str(normalized["memory_id"]).strip()
    if not normalized["memory_id"]:
        raise MemoryStoreError("Memory record memory_id cannot be empty")
    normalized["key"] = str(normalized["key"]).strip()
    if not normalized["key"]:
        raise MemoryStoreError("Memory record key cannot be empty")
    if len(normalized["key"]) > MEMORY_MAX_KEY_CHARS:
        raise MemoryStoreError("Memory record key exceeds maximum length")
    source_type = str(normalized["source"].get("type", "")).strip()
    if not source_type:
        raise MemoryStoreError("Memory record source.type cannot be empty")
    normalized["source"] = dict(normalized["source"])
    normalized["source"]["type"] = source_type
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



def _normalize_legacy_context(context: Any) -> list[str]:
    if isinstance(context, Mapping):
        flattened = []
        for key, value in context.items():
            try:
                rendered = json.dumps(
                    value,
                    ensure_ascii=False,
                    default=str,
                    sort_keys=True,
                )
            except (TypeError, ValueError):
                rendered = str(value)
            flattened.append(f"{key}={rendered}")
        return _normalize_context(flattened)
    return _normalize_context(context)


_LEGACY_VERSIONED_BASE_FIELDS = frozenset({
    "key",
    "value",
    "source",
    "project_id",
    "task_id",
    "context",
    "created_at",
    "updated_at",
    "version",
})


def _is_legacy_versioned_record(record: Mapping[str, Any]) -> bool:
    # Every schema-2 record is legacy now that the durable identity and
    # invalidation fields are part of schema 3. Require the old core payload
    # so malformed records are still rejected instead of being guessed.
    return (
        not _MEMORY_REQUIRED_FIELDS.issubset(record)
        and _LEGACY_VERSIONED_BASE_FIELDS.issubset(record)
    )


def _migrate_versioned_record(
    record: Mapping[str, Any],
    *,
    migration_time: str,
) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise MemoryStoreError("Persisted memory record must be an object")

    if _MEMORY_REQUIRED_FIELDS.issubset(record):
        return _validate_record(record)

    if not _is_legacy_versioned_record(record):
        missing = _MEMORY_REQUIRED_FIELDS.difference(record)
        raise MemoryStoreError(
            "Memory record missing fields: "
            + ", ".join(sorted(missing))
        )

    migrated = dict(record)
    migrated["memory_id"] = str(
        migrated.get("memory_id") or f"mem-{uuid.uuid4().hex}"
    )
    migrated["key"] = str(migrated.get("key", "")).strip()
    if not migrated["key"]:
        raise MemoryStoreError("Legacy memory record has an empty key")

    kind = str(migrated.get("kind", "fact")).strip().lower()
    if kind not in MEMORY_KINDS:
        raise MemoryStoreError(f"Unknown memory kind: {kind}")
    migrated["kind"] = kind

    migrated["source"] = _normalize_source(migrated.get("source", "legacy"))
    if isinstance(migrated["source"], dict):
        migrated["source"].setdefault(
            "migrated_from_schema",
            LEGACY_VERSIONED_SCHEMA_VERSION,
        )
        migrated["source"].setdefault("migrated_at", migration_time)

    migrated["project_id"] = (
        str(migrated["project_id"]).strip()
        if migrated.get("project_id") is not None
        else None
    )
    migrated["task_id"] = (
        str(migrated["task_id"]).strip()
        if migrated.get("task_id") is not None
        else None
    )
    migrated["context"] = _normalize_legacy_context(migrated.get("context"))

    migrated["created_at"] = str(
        migrated.get("created_at") or migration_time
    )
    migrated["updated_at"] = str(
        migrated.get("updated_at") or migrated["created_at"]
    )

    try:
        migrated["version"] = max(1, int(migrated.get("version", 1)))
    except (TypeError, ValueError) as exc:
        raise MemoryStoreError("Legacy memory record version is invalid") from exc

    migrated["valid"] = bool(migrated.get("valid", True))
    migrated["supersedes"] = migrated.get("supersedes")
    migrated["invalidated_at"] = migrated.get("invalidated_at")
    migrated["invalidation_reason"] = migrated.get("invalidation_reason")

    return _validate_record(migrated)


def _migrate_versioned_document(
    memory: Mapping[str, Any],
) -> dict[str, Any]:
    records = memory.get("records")
    if not isinstance(records, list):
        raise MemoryStoreError("Persisted memory records must be a list")

    migration_time = _now()
    migrated_records = [
        _migrate_versioned_record(record, migration_time=migration_time)
        for record in records
    ]
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "records": migrated_records,
    }


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

    schema_version = raw.get("schema_version")

    if schema_version == LEGACY_VERSIONED_SCHEMA_VERSION:
        migrated = _migrate_versioned_document(raw)
        # Persist the upgrade so subsequent restarts do not repeatedly migrate
        # the same memory file. A read must remain usable even on a read-only FS.
        try:
            _save_document(migrated)
        except OSError:
            pass
        return migrated

    if schema_version != MEMORY_SCHEMA_VERSION:
        raise MemoryStoreError(
            f"Unsupported memory schema version: {schema_version!r}"
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

    with _memory_write_lock():
        schema_version = memory.get("schema_version")
        if schema_version in (LEGACY_VERSIONED_SCHEMA_VERSION, MEMORY_SCHEMA_VERSION) and "records" in memory:
            if schema_version == LEGACY_VERSIONED_SCHEMA_VERSION:
                document = _migrate_versioned_document(memory)
            else:
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
        if record_context and not any(
            requested_item in record_item or record_item in requested_item
            for requested_item in requested
            for record_item in record_context
        ):
            return False

    return True


_QUERY_STOPWORDS = frozenset({
    "the", "and", "or", "with", "from", "into", "work", "on", "for",
    "this", "that", "are", "is", "was", "were", "use", "make", "task",
    "project", "yang", "dan", "atau", "dengan", "untuk", "dari", "pada", "ke", "di",
})


def _query_terms(query: str) -> tuple[str, ...]:
    terms = {
        term
        for term in re.findall(r"\w+", query.lower(), flags=re.UNICODE)
        if len(term) >= 3 and term not in _QUERY_STOPWORDS
    }
    return tuple(sorted(terms, key=lambda item: (-len(item), item)))


def _record_fields(record: Mapping[str, Any]) -> dict[str, str]:
    return {
        "key": str(record.get("key", "")).lower(),
        "value": str(record.get("value", "")).lower(),
        "kind": str(record.get("kind", "")).lower(),
        "source": str(record.get("source", "")).lower(),
        "project_id": str(record.get("project_id", "")).lower(),
        "task_id": str(record.get("task_id", "")).lower(),
        "context": str(record.get("context", "")).lower(),
    }


def _record_matches_query(record: Mapping[str, Any], query: str) -> bool:
    terms = _query_terms(query)
    if not terms:
        return query.lower() in str(record.get("key", "")).lower()

    fields = _record_fields(record)
    return any(
        term in field
        for term in terms
        for field in fields.values()
    )


def _record_match_score(record: Mapping[str, Any], query: str) -> int:
    terms = _query_terms(query)
    if not terms:
        return 100 if query.lower() in str(record.get("key", "")).lower() else 0

    fields = _record_fields(record)
    score = 0
    for term in terms:
        if term == fields["key"]:
            score += 100
        elif term in fields["key"]:
            score += 50
        if term in fields["context"]:
            score += 30
        if term in fields["value"]:
            score += 20
        if term in fields["project_id"] or term in fields["task_id"]:
            score += 15
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

    if len(key) > MEMORY_MAX_KEY_CHARS:
        raise ValueError("Memory key exceeds maximum length")
    value_json = json.dumps(value, ensure_ascii=False, default=str)
    if len(value_json) > MEMORY_MAX_VALUE_JSON_CHARS:
        raise ValueError("Memory value exceeds maximum size")
    source_json = json.dumps(normalized_source, ensure_ascii=False, default=str)
    if len(source_json) > MEMORY_MAX_SOURCE_JSON_CHARS:
        raise ValueError("Memory source exceeds maximum size")

    with _memory_write_lock():
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
            _scope_score(
                record,
                project_id=project_id,
                task_id=task_id,
                context=normalized_context,
            ),
            _record_match_score(record, query),
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

    with _memory_write_lock():
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
