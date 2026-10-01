import os


# ============================================================
# DAFTAR ENVIRONMENT VARIABLE API KEY
# ============================================================

API_KEY_NAMES = [
    "GEMINI_API_KEY_1",
    "GEMINI_API_KEY_2",
    "GEMINI_API_KEY_3",
]


# ============================================================
# KEY MANAGER
# ============================================================

class KeyManager:

    def __init__(self):
        self.index = 0
        self.failed_keys = set()

    # ========================================================
    # JUMLAH KEY TERSEDIA
    # ========================================================

    def jumlah_key(self):
        """
        Menghitung jumlah API key yang tersedia
        di environment.
        """

        jumlah = 0

        for nama in API_KEY_NAMES:

            if os.getenv(nama):

                jumlah += 1

        return jumlah

    # ========================================================
    # MENGAMBIL KEY AKTIF
    # ========================================================

    def get_key(self):
        """
        Mengambil API key yang sedang aktif.

        Key yang sudah ditandai gagal akan dilewati.
        """

        total = len(API_KEY_NAMES)

        for _ in range(total):

            nama = API_KEY_NAMES[
                self.index
            ]

            if nama not in self.failed_keys:

                key = os.getenv(nama)

                if key:

                    return {
                        "name": nama,
                        "key": key
                    }

            self.index = (
                self.index + 1
            ) % total

        raise RuntimeError(
            "Tidak ada API key yang tersedia."
        )

    # ========================================================
    # MENANDAI KEY GAGAL
    # ========================================================

    def mark_failed(self, nama_key):
        """
        Menandai sebuah API key sebagai gagal.

        Setelah ditandai, key tersebut tidak akan
        dipilih sampai manager di-reset.
        """

        self.failed_keys.add(
            nama_key
        )

        # Pindah ke key berikutnya.

        total = len(API_KEY_NAMES)

        for _ in range(total):

            self.index = (
                self.index + 1
            ) % total

            nama_berikutnya = API_KEY_NAMES[
                self.index
            ]

            if (
                nama_berikutnya
                not in self.failed_keys
                and os.getenv(nama_berikutnya)
            ):

                return

        # Semua key sudah gagal.

    # ========================================================
    # RESET
    # ========================================================

    def reset(self):
        """
        Menghapus status gagal seluruh key
        dan kembali ke key pertama.
        """

        self.index = 0

        self.failed_keys.clear()

    # ========================================================
    # STATUS
    # ========================================================

    def status(self):
        """
        Mengembalikan status Key Manager.
        """

        tersedia = []

        for nama in API_KEY_NAMES:

            if os.getenv(nama):

                tersedia.append(
                    nama
                )

        return {
            "key_tersedia": tersedia,
            "key_gagal": list(
                self.failed_keys
            ),
            "key_aktif": (
                API_KEY_NAMES[self.index]
                if API_KEY_NAMES
                else None
            ),
        }
