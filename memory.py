import json
import os


BASE_DIR = os.path.realpath(
    os.path.expanduser("~/ai-agent")
)

MEMORY_FILE = os.path.join(
    BASE_DIR,
    "memory.json"
)


def load_memory():
    """
    Membaca seluruh memory dari memory.json.
    """

    if not os.path.exists(MEMORY_FILE):
        return {}

    try:

        with open(
            MEMORY_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            return json.load(file)

    except json.JSONDecodeError:

        return {}


def save_memory(memory):
    """
    Menyimpan seluruh memory ke memory.json.
    """

    with open(
        MEMORY_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            memory,
            file,
            ensure_ascii=False,
            indent=2
        )


def remember(key, value):
    """
    Menyimpan sebuah informasi ke memory.
    """

    memory = load_memory()

    memory[key] = value

    save_memory(memory)

    return {
        "status": "success",
        "key": key,
        "value": value
    }


def recall(key):
    """
    Mengambil informasi berdasarkan key.
    """

    memory = load_memory()

    if key not in memory:

        return {
            "status": "not_found",
            "key": key
        }

    return {
        "status": "success",
        "key": key,
        "value": memory[key]
    }


def search_memory(query):
    """
    Mencari informasi di dalam memory
    berdasarkan teks pada key atau value.
    """

    memory = load_memory()

    query = str(query).lower()

    hasil = {}

    for key, value in memory.items():

        key_text = str(key).lower()
        value_text = str(value).lower()

        if (
            query in key_text
            or query in value_text
        ):

            hasil[key] = value

    if not hasil:

        return {
            "status": "not_found",
            "query": query,
            "hasil": {}
        }

    return {
        "status": "success",
        "query": query,
        "hasil": hasil
    }
