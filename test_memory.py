from memory import (
    remember,
    recall,
)


print("=== TEST MEMORY ===")


print("\n1. Menyimpan memory:")

hasil = remember(
    "nama_user",
    "Fariz"
)

print(hasil)


print("\n2. Membaca memory:")

hasil = recall(
    "nama_user"
)

print(hasil)


print("\n3. Mencari memory yang tidak ada:")

hasil = recall(
    "warna_favorit"
)

print(hasil)
