from __future__ import annotations

import re
from typing import Any


_FILE_NAME = re.compile(
    r"\bfile\s+([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.[A-Za-z0-9]{1,12})",
    re.IGNORECASE,
)
_ANY_FILE_NAME = re.compile(
    r"\b[A-Za-z0-9_-][A-Za-z0-9_.-]*\.[A-Za-z0-9]{1,12}\b"
)
_EXACT_CONTENT = re.compile(
    r"\b(?:isi|konten|content)\b.{0,60}?"
    r"\b(?:persis|tepat|exact(?:ly)?)\b"
    r"(?:.{0,30}?\b(?:berikut|as\s+follows)\b)?"
    r"\s*[:：]\s*(.*)\Z",
    re.IGNORECASE | re.DOTALL,
)

def extract_trusted_file_intent(goal: str) -> dict[str, Any] | None:
    """Extract exact file arguments; ambiguity must be handled as a denial upstream."""
    if not isinstance(goal, str) or not goal.strip():
        return None

    file_matches = list(_FILE_NAME.finditer(goal))
    all_file_tokens = list(_ANY_FILE_NAME.finditer(goal))
    content_match = _EXACT_CONTENT.search(goal)
    if (
        len(file_matches) != 1
        or len(all_file_tokens) != 1
        or content_match is None
        or all_file_tokens[0].group(0) != file_matches[0].group(1)
    ):
        return None

    relative_name = file_matches[0].group(1)
    if relative_name.startswith("/") or any(
        part in {"", ".", ".."} for part in relative_name.split("/")
    ):
        return None

    raw_content = content_match.group(1)
    # Only a blank-line boundary immediately before a recognized follow-up
    # instruction ends the payload. Other blank lines are part of file content.
    follow_up = re.search(
        r"\r?\n[ \t]*\r?\n(?=[ \t]*(?:baca kembali|verifikasi|laporkan|"
        r"jangan menyatakan|read back|verify|report)\b)",
        raw_content,
        re.IGNORECASE,
    )
    if follow_up:
        raw_content = raw_content[:follow_up.start()]
    content = raw_content.rstrip("\r\n")
    if not content.strip():
        return None
    return {"nama": relative_name, "isi": content}
