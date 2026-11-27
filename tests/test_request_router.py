import pytest

from core.request_router import is_action_request


@pytest.mark.parametrize(
    "message",
    [
        "Buat file catatan.txt",
        "Buatkan file catatan.txt",
        "Bikin file catatan.txt",
        "Bikinkan file catatan.txt",
        "Tuliskan isi laporan.txt",
        "Tolong buatkan file catatan.txt",
        "Coba buat file catatan.txt",
        "Tolong coba bikinkan file catatan.txt",
        "Bisa kamu buatkan file catatan.txt?",
        "Jalankan pengujian proyek",
        "Perbaiki bug di router",
    ],
)
def test_explicit_action_variants_enter_execution_route(message):
    assert is_action_request(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "Apa yang bisa kamu buatkan?",
        "Bisa kamu buatkan apa?",
        "Buatkan apa yang bagus?",
        "Bagaimana cara membuat file?",
        "Jelaskan cara kerja router",
        "Saya ingin belajar membuat aplikasi",
        "Jangan buat file apa pun",
        "Tolong jangan jalankan perintah",
        "",
        "   ",
    ],
)
def test_questions_explanations_and_negations_stay_out_of_execution_route(message):
    assert is_action_request(message) is False


def test_invalid_input_type_stays_out_of_execution_route():
    assert is_action_request(None) is False
    assert is_action_request(123) is False
