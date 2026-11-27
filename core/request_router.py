from __future__ import annotations

import re


# Only clear, imperative action requests enter the task planner. Explanations,
# questions about how to do something, and general conversation stay in chat.
_ACTION_PREFIX = re.compile(
    r"^(?:tolong\\s+|silakan\\s+|please\\s+)?"
    r"(?:buat|bikin|tulis|ubah|edit|hapus|delete|install|pasang|"
    r"jalankan|run|eksekusi|periksa|cek|lihat|baca|cari|unduh|download|"
    r"upload|commit|push|build|test|uji|perbaiki|implementasikan|"
    r"selesaikan|kerjakan|lanjutkan|resume|deploy|"
    r"create|write|modify|remove|execute|inspect|read|search|fix|"
    r"implement|complete)\\b",
    re.IGNORECASE,
)

_REQUEST_PREFIX = re.compile(
    r"^(?:bisa|dapat)\\s+(?:kamu\\s+)?"
    r"(?:buat|bikin|tulis|ubah|edit|hapus|install|pasang|jalankan|"
    r"cek|periksa|lihat|baca|cari|perbaiki|kerjakan|selesaikan)\\b",
    re.IGNORECASE,
)


def is_action_request(message: str) -> bool:
    """Return True only for an explicit imperative/action-style request."""
    if not isinstance(message, str) or not message.strip():
        return False
    normalized = re.sub(r"\\s+", " ", message.strip())
    return bool(_ACTION_PREFIX.match(normalized) or _REQUEST_PREFIX.match(normalized))
