import pytest

from permissions import get_permission


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        ("lihat", "safe"),
        ("lokasi", "safe"),
        ("siapa", "safe"),
        ("baca_file", "safe"),
        ("buat_folder", "confirm"),
        ("buat_file", "confirm"),
        ("tulis_file", "confirm"),
        ("jalankan_python", "confirm"),
        ("tool_tidak_dikenal", "blocked"),
    ],
)
def test_permission(tool, expected):
    assert get_permission(tool) == expected
