from permissions import get_permission


def test_permission(nama_tool):
    permission = get_permission(nama_tool)

    print(
        f"{nama_tool:20} -> {permission}"
    )


print("=== TEST PERMISSION SYSTEM ===")

test_permission("lihat")
test_permission("lokasi")
test_permission("siapa")
test_permission("baca_file")

test_permission("buat_folder")
test_permission("buat_file")
test_permission("tulis_file")
test_permission("jalankan_python")

test_permission("tool_tidak_dikenal")
