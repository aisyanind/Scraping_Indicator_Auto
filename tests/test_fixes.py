import json
import sys
sys.path.insert(0, "D:\\MagangHub\\Scraping_Indikator_Auto")

from engine.run_indicator import _sources_in_formula
from engine.formula import calculate_formula
from engine.instructions import parse_instruction
from extractors.discovery import (
    _collect_links,
    _extract_api_templates,
    _extract_json_result,
)
from sources.legacy_bridge import _error_result, _ok
from sources.generic_auto_extractor import (
    _extract_from_html_tables,
    _extract_from_json_data,
)
from extractors.pdf import _ocr_line_fallback
from extractors.flip_pdf import (
    _province_summary_value,
    _province_value,
    _table_value,
    _table_years,
)
from sources.tst_source import _map_kalsel


def test_rumus_empty_defaults_to_s1():
    """Rumus kosong harus dianggap S1, konsisten dengan calculate_formula."""
    assert _sources_in_formula("") == {1}
    assert _sources_in_formula(None) == {1}
    # calculate_formula("") juga default "S1"
    assert calculate_formula("", {"S1": 50}) == 50.0


def test_rumus_constant_no_source():
    """Rumus konstanta (tanpa S1/S2/S3) tidak mensyaratkan sumber apa pun."""
    assert _sources_in_formula("100") == set()
    assert _sources_in_formula("(1+2)*3") == set()


def test_rumus_references_sources():
    """Rumus dengan S1/S2/S3 mengembalikan set yang sesuai."""
    assert _sources_in_formula("S1") == {1}
    assert _sources_in_formula("(S1/S2)*100") == {1, 2}
    assert _sources_in_formula("S1+S2+S3") == {1, 2, 3}
    assert _sources_in_formula("S3") == {3}


def test_unused_source_failure_does_not_become_required():
    """Formula hanya mewajibkan slot sumber yang benar-benar dirujuk."""
    assert _sources_in_formula("(S1/S2)*100") == {1, 2}
    assert 3 not in _sources_in_formula("(S1/S2)*100")


def test_ok_empty_dict_returns_data_tidak_ada():
    """_ok() dengan dict kosong harus return DATA_TIDAK_ADA, bukan SUCCESS."""
    result = _ok({}, "ipk")
    assert result.status == "DATA_TIDAK_ADA"
    assert result.value is None


def test_ok_empty_list_returns_data_tidak_ada():
    """_ok() dengan list kosong harus return DATA_TIDAK_ADA."""
    result = _ok([], "tst")
    assert result.status == "DATA_TIDAK_ADA"


def test_ok_none_returns_data_tidak_ada():
    """_ok() dengan None harus return DATA_TIDAK_ADA."""
    result = _ok(None, "aki")
    assert result.status == "DATA_TIDAK_ADA"


def test_ok_valid_value_returns_success():
    """_ok() dengan nilai valid harus return SUCCESS."""
    result = _ok(50.0, "ipk")
    assert result.status == "SUCCESS"
    assert result.value == 50.0

    result = _ok({"Prov. Kalsel": 50.0}, "ipk")
    assert result.status == "SUCCESS"


def test_ojk_missing_data_returns_data_tidak_ada():
    """Parser OJK yang berhasil berjalan tetapi datanya kosong bukan parser hilang."""
    result = _error_result(
        RuntimeError("Dataset 107 getData() tidak memiliki row untuk 2020-12."),
        "ojk",
    )
    assert result.status == "DATA_TIDAK_ADA"

    result = _error_result(
        RuntimeError(
            "KALIMANTAN SELATAN tidak ditemukan pada row Dataset 107 "
            "untuk 2022-12."
        ),
        "ojk",
    )
    assert result.status == "DATA_TIDAK_ADA"


def test_ojk_unknown_dataset_stays_legacy_failed():
    """Dataset OJK yang belum didukung tetap meminta parser baru di router."""
    result = _error_result(
        NotImplementedError("Dataset OJK 999 belum mempunyai parser."),
        "ojk",
    )
    assert result.status == "LEGACY_FAILED"


def test_tst_map_kalsel_filter_year():
    """_map_kalsel() harus memfilter baris berdasarkan tahun."""
    rows = [
        {"nama_provinsi": "Kalimantan Selatan", "nama_kabkota": "Kab. Banjar",
         "tahun": "2023", "persen_penanganan": "50.0"},
        {"nama_provinsi": "Kalimantan Selatan", "nama_kabkota": "Kab. Banjar",
         "tahun": "2024", "persen_penanganan": "60.0"},
        {"nama_provinsi": "Kalimantan Selatan", "nama_kabkota": "Kota Banjarmasin",
         "tahun": "2024", "persen_penanganan": "70.0"},
    ]

    result_2023 = _map_kalsel(rows, 2023)
    assert result_2023 == {"Kab. Banjar": 50.0}

    result_2024 = _map_kalsel(rows, 2024)
    assert result_2024 == {"Kab. Banjar": 60.0, "Kota Banjarmasin": 70.0}

    result_2025 = _map_kalsel(rows, 2025)
    assert result_2025 == {}


def test_generic_extractors_filter_requested_year():
    """Generic HTML/JSON extractor harus memilih tahun yang diminta."""
    html = (
        "<table><tr><th>Wilayah</th><th>Indeks 2023</th>"
        "<th>Indeks 2024</th></tr>"
        "<tr><td>Prov. Kalimantan Selatan</td><td>55</td>"
        "<td>66</td></tr></table>"
    )
    html_values = _extract_from_html_tables(
        html,
        ["indeks"],
        (0, 200),
        2024,
        "Prov. Kalimantan Selatan",
    )
    assert html_values[0][0] == 66

    data = {
        "rows": [
            {"tahun": "2023", "wilayah": "Kalsel", "indeks": "55"},
            {"tahun": "2024", "wilayah": "Kalsel", "indeks": "66"},
        ]
    }
    json_values = _extract_from_json_data(
        data,
        ["indeks"],
        (0, 200),
        2024,
        "Prov. Kalimantan Selatan",
    )
    assert json_values[0][0] == 66


def test_discovery_finds_common_api_paths():
    """Discovery mengenali endpoint data yang tidak diawali /api/."""
    quote = chr(34)
    script = (
        "fetch(" + quote + "/v1/datasets" + quote + ")"
        "+fetch(" + quote + "/data/indeks" + quote + ")"
        "+import(" + quote + "/static/app.js" + quote + ")"
    )
    found = _extract_api_templates(script)
    assert "/v1/datasets" in found
    assert "/data/indeks" in found
    assert "/static/app.js" not in found


def test_network_json_uses_existing_extractor():
    """Respons API browser diproses dengan aturan tahun yang sama."""
    payload = {
        "rows": [
            {"tahun": 2023, "wilayah": "Kalsel", "indeks": 55},
            {"tahun": 2024, "wilayah": "Kalsel", "indeks": 66},
        ]
    }
    result = _extract_json_result(
        json.dumps(payload).encode(),
        parse_instruction("indeks Prov. Kalimantan Selatan"),
        2024,
        source_type="browser:network",
    )
    assert result is not None
    assert result.value == 66


def test_discovery_finds_digital_book_embeds():
    """Viewer buku digital dapat menunjuk file dari iframe/embed."""
    html = (
        '<iframe src="/viewer/book-2024.pdf?download=1"></iframe>'
        '<embed src="/files/book-2023.pdf?x=1">'
    )
    links = _collect_links(
        html,
        "https://example.test/catalog",
        parse_instruction("indeks Prov. Kalimantan Selatan"),
    )
    assert "https://example.test/viewer/book-2024.pdf?download=1" in links
    assert "https://example.test/files/book-2023.pdf?x=1" in links


def test_ocr_line_fallback_returns_review():
    """Nilai dari OCR tidak boleh langsung dianggap terverifikasi."""
    result = _ocr_line_fallback(
        "Provinsi 2024\nKalimantan Selatan 123,45\n",
        parse_instruction("indeks Prov. Kalimantan Selatan"),
        2024,
    )
    assert result is not None
    assert result.status == "REVIEW"
    assert result.value == 123.45


def test_flip_pdf_explicit_year_and_table_value():
    """Flip PDF mengambil nilai tahun dan baris wilayah yang tepat."""
    province_page = (
        "Provinsi Kalimantan Selatan NILAI IRB 2023: 80.00 "
        "190.00 180.00 129.44 (SEDANG)"
    )
    assert _province_value(province_page, 2023) == 129.44

    table_page = (
        "Tabel nilai indeks risiko Kalimantan Selatan "
        "2015 2016 2017 2018 2019 2020 2021 2022 2023 "
        "1 TANAH LAUT 178.00 178.00 169.22 TINGGI"
    )
    assert _table_value(table_page, "Kab. Tanah Laut", 2017) == 169.22

    summary_page = (
        "TABEL INDEKS RISIKO BENCANA PROVINSI TAHUN 2024 "
        "IRBI 2023 IRBI 2024 22 KALIMANTAN SELATAN 129.44 122.11 TURUN"
    ).lower()
    assert _province_summary_value(
        summary_page,
        "Prov. Kalimantan Selatan",
        2024,
    ) == 122.11


def test_flip_pdf_table_years_beyond_sequence():
    """Deret tahun yang melewati YEAR_SEQUENCE tidak boleh crash."""
    # 11 tahun: 2015-2024 (sequence penuh) + 2025 -> dulu IndexError.
    assert _table_years(
        "2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025"
    ) == list(range(2015, 2025))

    # Sequence pendek tetap dipetakan ke index tahun yang benar.
    page = (
        "tabel nilai indeks risiko 2015 2016 2017 2018 2019 2020 "
        "2021 2022 2023 2024 2025 "
        "1 tanah laut 178,00 178,00 169,22 TINGGI"
    )
    assert _table_value(page, "Kab. Tanah Laut", 2017) == 169.22


if __name__ == "__main__":
    test_rumus_empty_defaults_to_s1()
    test_rumus_constant_no_source()
    test_rumus_references_sources()
    test_ok_empty_dict_returns_data_tidak_ada()
    test_ok_empty_list_returns_data_tidak_ada()
    test_ok_none_returns_data_tidak_ada()
    test_ok_valid_value_returns_success()
    test_tst_map_kalsel_filter_year()
    test_generic_extractors_filter_requested_year()
    test_discovery_finds_common_api_paths()
    test_network_json_uses_existing_extractor()
    test_discovery_finds_digital_book_embeds()
    test_ocr_line_fallback_returns_review()
    test_flip_pdf_explicit_year_and_table_value()
    test_flip_pdf_table_years_beyond_sequence()
    print("ALL TESTS PASSED")