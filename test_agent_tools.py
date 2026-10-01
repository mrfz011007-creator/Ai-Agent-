from agent import (
    jalankan_tool,
    proses_tool,
)


print("=== TEST TOOL EXECUTION ===")


print("\n1. Test safe tool:")

hasil = proses_tool(
    "lokasi",
    {}
)

print(hasil)


print("\n2. Test unknown tool:")

hasil = proses_tool(
    "tool_tidak_dikenal",
    {}
)

print(hasil)


print("\n3. Test confirm tool:")

print(
    "Tes berikut akan meminta konfirmasi."
)

hasil = proses_tool(
    "buat_file",
    {
        "nama": "test_permission.txt"
    }
)

print(hasil)
