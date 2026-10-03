"""Tests for the public permission lookup API."""

import pytest

from permissions import get_permission


@pytest.mark.parametrize(
    "nama_tool, expected",
    [
        ("lihat", "safe"),
        ("lokasi", "safe"),
        ("siapa", "safe"),
        ("baca_file", "safe"),
        ("cari_teks", "safe"),
        ("buat_folder", "confirm"),
        ("buat_file", "confirm"),
        ("tulis_file", "confirm"),
        ("patch_file", "confirm"),
        ("jalankan_python", "confirm"),
        ("run_command", "confirm"),
    ],
)
def test_permission(nama_tool, expected):
    assert get_permission(nama_tool) == expected


def test_unknown_tool_permission_is_blocked():
    assert get_permission("tool_tidak_dikenal") == "blocked"
