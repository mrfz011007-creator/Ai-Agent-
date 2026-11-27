from core.trusted_intent import extract_trusted_file_intent, is_file_creation_request


def test_extract_exact_multiline_file_request_and_stop_before_followup_instruction():
    request = (
        "Buat file uji_agent_02.txt di workspace dengan isi persis berikut:\n"
        "Fariz Agent Test\n"
        "Tahap: 2\n"
        "Status: berhasil\n"
        "\n"
        "Baca kembali file tersebut dan verifikasi seluruh isinya sama persis."
    )

    assert extract_trusted_file_intent(request) == {
        "nama": "uji_agent_02.txt",
        "isi": "Fariz Agent Test\nTahap: 2\nStatus: berhasil",
    }


def test_extract_exact_file_request_with_inline_content():
    request = "Buat file uji_agent_01.txt dengan isi persis: Fariz Agent Test\nTahap: 1"
    assert extract_trusted_file_intent(request) == {
        "nama": "uji_agent_01.txt",
        "isi": "Fariz Agent Test\nTahap: 1",
    }


def test_ambiguous_or_non_exact_file_request_does_not_create_constraints():
    assert extract_trusted_file_intent("Buat file catatan.txt") is None
    assert extract_trusted_file_intent(
        "Buat file ../catatan.txt dengan isi persis: halo"
    ) is None
    assert extract_trusted_file_intent(
        "Buat file satu.txt dan dua.txt dengan isi persis: halo"
    ) is None



def test_extract_preserves_blank_lines_inside_file_content():
    request = (
        "Buat file catatan.txt dengan isi persis berikut:\n"
        "Bagian pertama\n"
        "\n"
        "Bagian kedua\n"
        "\n"
        "Baca kembali file tersebut dan verifikasi isinya."
    )
    assert extract_trusted_file_intent(request) == {
        "nama": "catatan.txt",
        "isi": "Bagian pertama\n\nBagian kedua",
    }


def test_unrecognized_exact_file_format_remains_untrusted():
    request = "Buat file catatan.txt dengan isi tepat seperti berikut\nIsi yang diminta"
    assert extract_trusted_file_intent(request) is None



def test_explicit_file_creation_is_detected_even_when_format_is_unsupported():
    assert is_file_creation_request("Buat file catatan.txt dengan isi tepat seperti berikut")
    assert is_file_creation_request("Create a file notes.txt with exact contents")
    assert not is_file_creation_request("Jelaskan cara kerja file system")



def test_extract_preserves_one_trailing_newline_as_file_content():
    request = "Buatkan file catatan.txt dengan isi persis: Fariz Agent\n"
    assert extract_trusted_file_intent(request) == {
        "nama": "catatan.txt",
        "isi": "Fariz Agent\n",
    }


def test_extract_preserves_crlf_content_without_normalizing_line_endings():
    request = "Bikinkan file catatan.txt dengan isi persis: baris 1\r\nbaris 2\r\n"
    assert extract_trusted_file_intent(request) == {
        "nama": "catatan.txt",
        "isi": "baris 1\r\nbaris 2\r\n",
    }


def test_file_creation_variants_are_recognized_for_fail_closed_handling():
    assert is_file_creation_request("Buatkan file catatan.txt dengan isi persis: halo")
    assert is_file_creation_request("Bikinkan file catatan.txt dengan isi persis: halo")
    assert is_file_creation_request("Tuliskan file catatan.txt dengan isi persis: halo")


def test_extract_preserves_leading_indentation_and_trailing_blank_lines():
    request = (
        "Buat file catatan.txt dengan isi persis berikut:\n"
        "  Fariz\n"
        "\tTahap\n"
        "\n"
    )
    assert extract_trusted_file_intent(request) == {
        "nama": "catatan.txt",
        "isi": "  Fariz\n\tTahap\n\n",
    }


def test_extract_preserves_an_intentional_leading_blank_line():
    request = "Buat file catatan.txt dengan isi persis berikut:\n\nFariz"
    assert extract_trusted_file_intent(request) == {
        "nama": "catatan.txt",
        "isi": "\nFariz",
    }


def test_extract_removes_only_one_inline_separator_space():
    request = "Buat file catatan.txt dengan isi persis:   Fariz"
    assert extract_trusted_file_intent(request) == {
        "nama": "catatan.txt",
        "isi": "  Fariz",
    }
