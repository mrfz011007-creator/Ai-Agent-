import os
import subprocess


BASE_DIR = os.path.realpath(
    os.path.expanduser("~/ai-agent")
)


def path_aman(nama):
    """
    Mengubah path menjadi absolute path dan memastikan
    path tersebut tetap berada di dalam BASE_DIR.
    """

    path = os.path.realpath(
        os.path.join(BASE_DIR, nama)
    )

    if path == BASE_DIR:
        return path

    if not path.startswith(BASE_DIR + os.sep):
        raise PermissionError(
            "Akses ditolak: path berada di luar project agent."
        )

    return path


def jalankan(command):
    hasil = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=BASE_DIR
    )

    return {
        "stdout": hasil.stdout,
        "stderr": hasil.stderr,
        "returncode": hasil.returncode
    }


def lihat():
    return jalankan(
        ["ls", "-la"]
    )


def lokasi():
    return jalankan(
        ["pwd"]
    )


def siapa():
    return jalankan(
        ["whoami"]
    )


def buat_folder(nama):
    try:

        path = path_aman(nama)

        os.mkdir(path)

        return {
            "status": "success",
            "pesan": f"Folder berhasil dibuat: {nama}"
        }

    except FileExistsError:

        return {
            "status": "error",
            "pesan": f"Folder sudah ada: {nama}"
        }

    except Exception as error:

        return {
            "status": "error",
            "pesan": str(error)
        }


def buat_file(nama):
    try:

        path = path_aman(nama)

        with open(
            path,
            "x",
            encoding="utf-8"
        ):
            pass

        return {
            "status": "success",
            "pesan": f"File berhasil dibuat: {nama}"
        }

    except FileExistsError:

        return {
            "status": "error",
            "pesan": f"File sudah ada: {nama}"
        }

    except Exception as error:

        return {
            "status": "error",
            "pesan": str(error)
        }


def baca_file(nama):
    try:

        path = path_aman(nama)

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as file:

            isi = file.read()

        return {
            "status": "success",
            "isi": isi
        }

    except FileNotFoundError:

        return {
            "status": "error",
            "pesan": f"File tidak ditemukan: {nama}"
        }

    except PermissionError:

        return {
            "status": "error",
            "pesan": f"Tidak punya izin membaca file: {nama}"
        }

    except Exception as error:

        return {
            "status": "error",
            "pesan": str(error)
        }


def tulis_file(nama, isi):
    try:

        path = path_aman(nama)

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as file:

            file.write(isi)

        return {
            "status": "success",
            "pesan": f"File berhasil ditulis: {nama}"
        }

    except PermissionError:

        return {
            "status": "error",
            "pesan": f"Tidak punya izin menulis file: {nama}"
        }

    except Exception as error:

        return {
            "status": "error",
            "pesan": str(error)
        }


def jalankan_python(nama):
    try:

        path = path_aman(nama)

        return jalankan(
            ["python", path]
        )

    except Exception as error:

        return {
            "status": "error",
            "pesan": str(error)
        }
