from __future__ import annotations

import re
from typing import Any

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{12,}"),
    re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"(?i)(gemini[_-]?api[_-]?key[_-]?\d*\s*[=:]\s*)[^\s,;]+"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)

def redact_text(value: str) -> str:
    result = value
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith(r"\bAIza") or pattern.pattern.startswith(r"\bsk-"):
            result = pattern.sub("[REDACTED_SECRET]", result)
        else:
            result = pattern.sub(lambda match: f"{match.group(1)}[REDACTED_SECRET]", result)
    return result

def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    return value
