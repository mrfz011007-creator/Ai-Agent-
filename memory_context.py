from memory import (
    recall,
    search_memory,
)


def recall_memory(key):
    """
    Mengambil informasi dari long-term memory
    berdasarkan key.
    """

    return recall(key)


def search_memory_tool(query):
    """
    Mencari informasi di long-term memory
    berdasarkan key atau isi value.
    """

    return search_memory(query)


def ambil_memory(key=None, query=None):
    """
    Interface umum untuk mengambil informasi
    dari long-term memory.

    Jika key diberikan, gunakan recall.

    Jika query diberikan, gunakan search_memory.

    Jika keduanya diberikan, key diprioritaskan.
    """

    if key is not None:

        return recall_memory(key)

    if query is not None:

        return search_memory_tool(query)

    return {
        "status": "error",
        "pesan": (
            "Harus memberikan key "
            "atau query."
        )
    }
