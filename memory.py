from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def memory_file() -> Path:
    root = Path(
        os.environ.get("AI_AGENT_WORKSPACE_ROOT", os.getcwd())
    ).resolve()
    return root / "memory.json"


def load_memory():
    path = memory_file()
    if not path.exists():
        return {}

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise RuntimeError("MEMORY_CORRUPTED_OR_UNREADABLE") from error
    if not isinstance(value, dict):
        raise RuntimeError("MEMORY_FORMAT_INVALID")
    return value


def save_memory(memory):
    if not isinstance(memory, dict):
        raise TypeError("memory must be an object")

    path = memory_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(memory, ensure_ascii=False, indent=2)
    fd, temporary = tempfile.mkstemp(
        prefix=".memory-", suffix=".tmp", dir=path.parent
    )
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


def remember(key, value):
    memory = load_memory()
    memory[key] = value
    save_memory(memory)
    return {
        "status": "success",
        "success": True,
        "key": key,
        "value": value,
    }


def recall(key):
    memory = load_memory()
    if key not in memory:
        return {
            "status": "not_found",
            "success": True,
            "key": key,
        }

    return {
        "status": "success",
        "success": True,
        "key": key,
        "value": memory[key],
    }


def search_memory(query):
    memory = load_memory()
    query = str(query).lower()
    hasil = {
        key: value
        for key, value in memory.items()
        if query in str(key).lower() or query in str(value).lower()
    }

    if not hasil:
        return {
            "status": "not_found",
            "success": True,
            "query": query,
            "hasil": {},
        }

    return {
        "status": "success",
        "success": True,
        "query": query,
        "hasil": hasil,
    }
