import re


# ============================================================
# NORMALISASI TEKS
# ============================================================

def normalisasi_teks(teks):
    """
    Menormalkan teks agar variasi penulisan sederhana
    dapat diproses dengan konsisten.
    """

    teks = str(teks).strip().lower()

    teks = re.sub(
        r"[^\w\s]",
        " ",
        teks
    )

    pengganti = {
        "aku": "saya",
        "ku": "saya",
        "gue": "saya",
        "gw": "saya",
        "dimana": "di mana",
    }

    kata = teks.split()

    hasil = []

    for item in kata:

        hasil.append(
            pengganti.get(
                item,
                item
            )
        )

    return " ".join(
        hasil
    )


# ============================================================
# INTENT MEMORY
# ============================================================

MEMORY_INTENTS = {

    "RECALL_NAMA_USER": {
        "key": "nama_user",
        "patterns": [
            ["siapa", "nama", "saya"],
            ["nama", "saya", "siapa"],
            ["nama", "saya", "apa"],
            ["ingat", "nama", "saya"],
        ],
    },

    "RECALL_KOTA": {
        "key": "kota",
        "patterns": [
            ["kota", "saya", "apa"],
            ["saya", "tinggal", "di", "mana"],
            ["ingat", "kota", "saya"],
        ],
    },

    "RECALL_BAHASA": {
        "key": "bahasa",
        "patterns": [
            ["bahasa", "pemrograman", "saya"],
            ["bahasa", "yang", "saya", "gunakan"],
            ["bahasa", "saya", "apa"],
            ["ingat", "bahasa", "saya"],
        ],
    },

    "RECALL_FRAMEWORK": {
        "key": "framework",
        "patterns": [
            ["framework", "saya", "apa"],
            ["framework", "yang", "saya", "gunakan"],
            ["ingat", "framework", "saya"],
        ],
    },
}


# ============================================================
# INTENT LOCAL COMMAND
# ============================================================

LOCAL_INTENTS = {

    "SHOW_LOCATION": {
        "tool": "lokasi",
        "patterns": [
            ["lokasi", "saya"],
            ["di", "mana", "saya"],
            ["direktori", "saya"],
            ["pwd"],
        ],
    },

    "SHOW_USER": {
        "tool": "siapa",
        "patterns": [
            ["siapa", "user", "saya"],
            ["username", "saya"],
            ["user", "saya"],
            ["whoami"],
        ],
    },

    "LIST_FILES": {
        "tool": "lihat",
        "patterns": [
            ["lihat", "file"],
            ["lihat", "folder"],
            ["isi", "folder"],
            ["daftar", "file"],
            ["ls"],
        ],
    },
}


# ============================================================
# MATCHING
# ============================================================

def pola_cocok(teks, pola):
    """
    Cocok hanya jika pola muncul sebagai rangkaian kata berurutan.
    """

    kata_teks = teks.split()
    pola = list(pola)

    if not pola or len(pola) > len(kata_teks):
        return False

    panjang = len(pola)
    return any(
        kata_teks[i:i + panjang] == pola
        for i in range(len(kata_teks) - panjang + 1)
    )


# ============================================================
# DETEKSI INTENT
# ============================================================

def deteksi_intent(perintah):
    """
    Mendeteksi intent dari perintah pengguna.

    Return dictionary jika ditemukan.

    Jika tidak ditemukan:
        None
    """

    teks = normalisasi_teks(
        perintah
    )

    # Negated local/memory commands must not be executed
    # through deterministic intent matching.
    negasi = {"jangan", "tidak", "tak", "bukan", "batalkan"}
    if any(kata in teks.split() for kata in negasi):
        return None

    # ========================================================
    # MEMORY INTENT
    # ========================================================

    for nama_intent, data in MEMORY_INTENTS.items():

        for pola in data["patterns"]:

            if pola_cocok(
                teks,
                pola
            ):

                return {
                    "intent": nama_intent,
                    "jenis": "memory",
                    "key": data["key"],
                    "tool": "recall",
                }

    # ========================================================
    # LOCAL INTENT
    # ========================================================

    for nama_intent, data in LOCAL_INTENTS.items():

        for pola in data["patterns"]:

            if pola_cocok(
                teks,
                pola
            ):

                return {
                    "intent": nama_intent,
                    "jenis": "local",
                    "tool": data["tool"],
                }

    # ========================================================
    # TIDAK DITEMUKAN
    # ========================================================

    return None
