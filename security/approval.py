from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def approval_digest(
    tool: str,
    action: str,
    arguments: Mapping[str, Any],
) -> str:
    """Return a stable digest binding approval to one exact tool request."""
    payload = {
        "tool": tool,
        "action": action,
        "arguments": arguments,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
