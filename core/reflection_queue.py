from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

MAX_PENDING_ITEMS = 500
MAX_TOTAL_ITEMS = 1000
MAX_QUEUE_FILE_BYTES = 12 * 1024 * 1024
MAX_QUEUE_CANDIDATE_CHARS = 10000

def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

def _path() -> Path:
    return Path(os.environ.get('AI_AGENT_WORKSPACE_ROOT', os.getcwd())).resolve() / 'reflection_queue.json'

def _load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return {'schema_version': 1, 'items': []}
    if path.stat().st_size > MAX_QUEUE_FILE_BYTES:
        raise RuntimeError('REFLECTION_QUEUE_TOO_LARGE')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, Mapping) or value.get('schema_version') != 1 or not isinstance(value.get('items'), list):
        raise RuntimeError('REFLECTION_QUEUE_INVALID')
    for item in value['items']:
        if (
            not isinstance(item, Mapping)
            or not isinstance(item.get('id'), str)
            or item.get('status') not in {'pending', 'approved', 'rejected'}
            or not isinstance(item.get('candidate'), Mapping)
            or len(json.dumps(item.get('candidate'), ensure_ascii=False, separators=(",", ":"))) > MAX_QUEUE_CANDIDATE_CHARS
        ):
            raise RuntimeError('REFLECTION_QUEUE_ITEM_INVALID')
    return dict(value)

def _save(value: Mapping[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.reflection-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
        raise

def enqueue(candidate: Mapping[str, Any], *, reason: str) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise ValueError('REFLECTION_QUEUE_CANDIDATE_INVALID')
    if len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":"))) > MAX_QUEUE_CANDIDATE_CHARS:
        raise ValueError('REFLECTION_QUEUE_CANDIDATE_TOO_LARGE')
    if not isinstance(reason, str) or len(reason) > 200:
        raise ValueError('REFLECTION_QUEUE_REASON_INVALID')
    store = _load()
    pending_count = sum(1 for entry in store['items'] if entry.get('status') == 'pending')
    if pending_count >= MAX_PENDING_ITEMS:
        raise RuntimeError('REFLECTION_QUEUE_FULL')
    if len(store['items']) >= MAX_TOTAL_ITEMS:
        pending = [entry for entry in store['items'] if entry.get('status') == 'pending']
        resolved = [entry for entry in store['items'] if entry.get('status') != 'pending']
        keep_resolved = max(0, MAX_TOTAL_ITEMS - len(pending) - 1)
        store['items'] = resolved[-keep_resolved:] + pending if keep_resolved else pending
    item = {'id': 'reflection-' + uuid.uuid4().hex[:12], 'status': 'pending', 'created_at': _now(),
            'reason': reason, 'candidate': dict(candidate)}
    store['items'].append(item)
    _save(store)
    return item

def list_pending() -> list[dict[str, Any]]:
    return [x for x in _load()['items'] if x.get('status') == 'pending']

def resolve(item_id: str, *, status: str, resolution: str) -> dict[str, Any]:
    if status not in {'approved', 'rejected'}:
        raise ValueError('invalid resolution status')
    store = _load()
    for item in store['items']:
        if item.get('id') == item_id:
            item['status'] = status
            item['resolved_at'] = _now()
            item['resolution'] = resolution
            _save(store)
            return item
    return {'status': 'not_found', 'id': item_id}