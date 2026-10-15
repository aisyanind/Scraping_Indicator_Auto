import json
from types import SimpleNamespace

from engine.run_indicator import (
    _sources_in_formula,
    _is_retryable_status,
    _build_failure_summary,
    process_indicator_job,
)
from engine.formula import calculate_formula
from engine.instructions import parse_instruction
from models import IndicatorJob
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
    _annual_snapshot_value,
    _edition_year,
    _province_summary_value,
    _province_value,
    _table_value,
    _table_years,
    extract_flip_pdf,
)
import extractors.flip_pdf as flip_pdf
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
    """Deret tahun dibaca dinamis, termasuk tahun setelah 2024."""
    assert _table_years(
        "2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025"
    ) == list(range(2015, 2026))

    # Sequence pendek tetap dipetakan ke index tahun yang benar.
    page = (
        "tabel nilai indeks risiko 2015 2016 2017 2018 2019 2020 "
        "2021 2022 2023 2024 2025 "
        "1 tanah laut 178,00 178,00 169,22 TINGGI"
    )
    assert _table_value(page, "Kab. Tanah Laut", 2017) == 169.22


def test_flip_pdf_annual_snapshot_2025():
    """Snapshot IRBI 2025 mengandung nilai provinsi dan kabupaten/kota."""
    province_table = (
        "Tabel Indeks Risiko Bencana Tahun 2025 Tingkat Provinsi "
        "NO PROVINSI SEB 2025 1 ACEH 181,05 192,75 "
        "22 KALIMANTAN SELATAN 83,79 83,68 "
        "23 KALIMANTAN TIMUR 114,56 117,73"
    )
    county_table = (
        "NO KABUPATEN/KOTA PROVINSI SKOR KELAS RISIKO "
        "289 TANAH LAUT KALIMANTAN SELATAN 131,76 SEDANG "
        "290 BREBES JAWA TENGAH 131,59 SEDANG"
    )

    assert _annual_snapshot_value(
        province_table,
        "Prov. Kalimantan Selatan",
        2025,
        True,
    ) == 83.68
    assert _annual_snapshot_value(
        county_table,
        "Kab. Tanah Laut",
        2025,
        False,
    ) == 131.76
    assert _edition_year(["IRBI Indeks Risiko Bencana Indonesia Tahun 2025"]) == 2025


def test_flip_pdf_reads_province_chart_year(monkeypatch):
    """Grafik provinsi dipetakan ke tahun dari caption text layer."""
    pages = [""] * 139
    pages.append(
        "Grafik nilai indeks risiko Provinsi Kalimantan Selatan "
        "dari Tahun 2015 sampai dengan tahun 2023"
    )
    image_requests = []
    chart_values = [
        151.60, 147.31, 147.31, 145.37, 145.37,
        144.81, 141.66, 128.81, 129.44,
    ]

    monkeypatch.setattr(flip_pdf, "_load_pages", lambda _url: pages)

    def fake_ocr(image_url, expected_count):
        image_requests.append((image_url, expected_count))
        return chart_values, 0.82

    monkeypatch.setattr(flip_pdf, "_ocr_chart_values", fake_ocr)
    payload = SimpleNamespace(
        content=b"Flip PDF Professional",
        final_url="https://example.test/reports/mobile/index.html",
        url="https://example.test/reports/mobile/index.html",
    )

    result = extract_flip_pdf(
        payload,
        parse_instruction("Indeks Risiko Bencana Prov. Kalimantan Selatan"),
        2020,
    )

    assert result.value == 144.81
    assert result.source_type == "flip_pdf:province_chart_ocr"
    assert image_requests == [
        ("https://example.test/reports/files/mobile/140.jpg", 9)
    ]


def test_flip_pdf_maps_repeated_values_in_ten_year_chart(monkeypatch):
    import extractors.flip_pdf as flip_pdf

    pages = [""] * 141
    pages.append(
        "Grafik nilai indeks risiko Provinsi Kalimantan Selatan "
        "dari Tahun 2015 sampai dengan tahun 2024"
    )
    chart_values = [
        151.60, 147.31, 147.31, 145.37, 145.37,
        144.81, 141.66, 128.81, 129.44, 122.11,
    ]
    requested_counts = []

    monkeypatch.setattr(flip_pdf, "_load_pages", lambda _url: pages)

    def fake_ocr(_image_url, expected_count):
        requested_counts.append(expected_count)
        return chart_values, 0.83

    monkeypatch.setattr(flip_pdf, "_ocr_chart_values", fake_ocr)
    payload = SimpleNamespace(
        content=b"Flip PDF Professional",
        final_url="https://example.test/reports/mobile/index.html",
        url="https://example.test/reports/mobile/index.html",
    )

    result = flip_pdf.extract_flip_pdf(
        payload,
        parse_instruction("Indeks Risiko Bencana Prov. Kalimantan Selatan"),
        2019,
    )

    assert result.value == 145.37
    assert result.source_type == "flip_pdf:province_chart_ocr"
    assert requested_counts == [10]


def test_retryable_statuses():
    """Status final tidak di-retry; status transient berhak retry."""
    for status in (
        "SUCCESS",
        "REVIEW",
        "DATA_TIDAK_ADA",
        "SUMBER_PERLU_LOGIN",
        "SUMBER_TERBLOKIR",
    ):
        assert _is_retryable_status(status) is False

    for status in ("NEED_PARSER", "LINK_TIDAK_LENGKAP", "LEGACY_FAILED"):
        assert _is_retryable_status(status) is True


def test_error_result_maps_cloudflare_block():
    """403/Cloudflare harus menjadi SUMBER_TERBLOKIR, bukan NEED_PARSER."""
    error_403 = RuntimeError(
        "403 Client Error: Forbidden for url: "
        "https://perpustakaan.bappenas.go.id/x.pdf"
    )
    result = _error_result(error_403, "ibei")
    assert result.status == "SUMBER_TERBLOKIR"

    error_blocked = RuntimeError(
        "SOURCE_BLOCKED: Chrome tidak bisa mengunduh PDF "
        "(terblokir proteksi Cloudflare)."
    )
    result = _error_result(error_blocked, "ibei")
    assert result.status == "SUMBER_TERBLOKIR"
    assert "SOURCE_BLOCKED" in result.message


def test_blocked_summary_includes_detail():
    """Ringkasan SUMBER_TERBLOKIR menyertakan detail error asli."""
    summary = _build_failure_summary(
        [
            {
                "tahun": 2024,
                "source": 1,
                "status": "SUMBER_TERBLOKIR",
                "message": "403 Client Error: Forbidden for url: ...",
            }
        ],
        used_sources={1},
        regions=["Prov. Kalimantan Selatan"],
    )

    assert "situs sumber memblokir akses otomatis" in summary
    assert "403 Client Error" in summary


def test_process_indicator_job_blocked_no_retry(monkeypatch):
    """SUMBER_TERBLOKIR tidak di-retry dan tampil sebagai status final."""
    from engine import run_indicator

    calls = []

    def blocked(**kwargs):
        calls.append(1)
        result = _FakeResult("SUMBER_TERBLOKIR")
        result.message = "403 Client Error: Forbidden for url: ..."
        return result

    monkeypatch.setattr(run_indicator, "scrape_source_auto", blocked)

    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *_: None)

    job = _make_job()
    output = process_indicator_job(job)

    assert len(calls) == 1
    assert output["status"] == "SUMBER_TERBLOKIR"
    assert "memblokir akses otomatis" in output["failure_summary"]
    assert "403 Client Error" in output["failure_summary"]


def _make_job(**overrides):
    base = {
        "Job_ID": "TEST-1",
        "Kode": "TEST",
        "Nama_Indikator": "Indikator Tes",
        "Tahun": "2024",
        "Wilayah": "Prov. Kalimantan Selatan",
        "Sumber_1": "https://example.test/data",
        "Data_Diambil_1": "indeks Prov. Kalimantan Selatan",
        "Sumber_2": "",
        "Data_Diambil_2": "",
        "Sumber_3": "",
        "Data_Diambil_3": "",
        "Rumus": "S1",
    }
    base.update(overrides)
    return IndicatorJob.from_dict(base)


class _FakeResult:
    def __init__(self, status, value=None):
        self.status = status
        self.value = value
        self.confidence = 1.0
        self.source_type = "html"
        self.message = "ok"
        self.evidence = []


def test_process_indicator_job_skips_existing_complete_year(monkeypatch):
    from engine import run_indicator

    calls = []

    def fake_scrape(**kwargs):
        calls.append(kwargs)
        return _FakeResult("SUCCESS", value=99.0)

    monkeypatch.setattr(run_indicator, "scrape_source_auto", fake_scrape)
    output = process_indicator_job(
        _make_job(),
        existing_results=[{
            "Kode": "TEST",
            "Wilayah": "Prov. Kalimantan Selatan",
            "Tahun": "2024",
            "Status": "SUCCESS",
        }],
    )

    assert calls == []
    assert output["status"] == "SUCCESS"
    assert output["results"] == []
    assert output["diagnostics"][0]["status"] == "SKIPPED_EXISTING"
    assert output["failure_summary"] == ""


def test_process_indicator_job_scrapes_if_a_requested_region_is_missing(monkeypatch):
    import config
    from engine import run_indicator

    calls = []

    def fake_scrape(**kwargs):
        calls.append(kwargs)
        return _FakeResult("DATA_TIDAK_ADA")

    monkeypatch.setattr(run_indicator, "scrape_source_auto", fake_scrape)
    regions = config.KALSEL_WILAYAHS[1:]
    existing_results = [
        {
            "Kode": "TEST",
            "Wilayah": region,
            "Tahun": "2024",
            "Status": "SUCCESS",
        }
        for region in regions[:-1]
    ]
    job = _make_job(
        Data_Diambil_1="Cakupan: seluruh Kab/Kota di Kalsel"
    )

    output = process_indicator_job(
        job,
        existing_results=existing_results,
    )

    # Tahun tetap di-scrape karena masih ada wilayah yang kurang.
    assert len(calls) == 1
    # Kegagalan scrape tidak menghasilkan baris untuk wilayah yang
    # sudah SUCCESS (tidak ditimpa DATA_TIDAK_ADA).
    for result in output["results"]:
        assert result["wilayah"] not in regions[:-1]


def test_process_indicator_job_partial_year_emits_only_missing_region(monkeypatch):
    """Tahun partial: hanya wilayah yang belum SUCCESS yang dapat baris hasil."""
    import config
    from engine import run_indicator

    def fake_scrape(**kwargs):
        # Scrape mengembalikan nilai untuk semua 13 kab/kota.
        return _FakeResult(
            "SUCCESS",
            value={region: 80.0 for region in config.KALSEL_WILAYAHS[1:]},
        )

    monkeypatch.setattr(run_indicator, "scrape_source_auto", fake_scrape)
    regions = config.KALSEL_WILAYAHS[1:]
    missing = regions[-1]
    existing_results = [
        {
            "Kode": "TEST",
            "Wilayah": region,
            "Tahun": "2024",
            "Status": "SUCCESS",
        }
        for region in regions[:-1]
    ]
    job = _make_job(
        Data_Diambil_1="Cakupan: seluruh Kab/Kota di Kalsel"
    )

    output = process_indicator_job(
        job,
        existing_results=existing_results,
    )

    # Hanya wilayah yang kurang yang menghasilkan baris.
    assert len(output["results"]) == 1
    assert output["results"][0]["wilayah"] == missing
    assert output["results"][0]["status"] == "SUCCESS"
    assert output["status"] == "SUCCESS"


def test_process_indicator_job_does_not_clobber_existing_on_failure(monkeypatch):
    """Scrape gagal: wilayah yang sudah SUCCESS tidak ditimpa DATA_TIDAK_ADA."""
    import config
    from engine import run_indicator

    def fake_scrape(**kwargs):
        return _FakeResult("DATA_TIDAK_ADA")

    monkeypatch.setattr(run_indicator, "scrape_source_auto", fake_scrape)
    regions = config.KALSEL_WILAYAHS[1:]
    existing_results = [
        {
            "Kode": "TEST",
            "Wilayah": region,
            "Tahun": "2024",
            "Status": "SUCCESS",
        }
        for region in regions[:-1]
    ]
    job = _make_job(
        Data_Diambil_1="Cakupan: seluruh Kab/Kota di Kalsel"
    )

    output = process_indicator_job(
        job,
        existing_results=existing_results,
    )

    # Tidak ada baris DATA_TIDAK_ADA untuk wilayah yang sudah SUCCESS.
    for result in output["results"]:
        assert result["wilayah"] not in regions[:-1]


def test_process_indicator_job_reports_progress(monkeypatch):
    """on_progress dipanggil dan memuat teks 'Percobaan N/M'."""
    import config
    from engine import run_indicator

    attempts_seen = []
    messages = []

    def fake_scrape(**kwargs):
        attempts_seen.append(kwargs["url"])
        # Gagal 2 kali dulu dengan NEED_PARSER, lalu sukses.
        if len(attempts_seen) < 3:
            return _FakeResult("NEED_PARSER")
        return _FakeResult("SUCCESS", value=42.0)

    monkeypatch.setattr(run_indicator, "scrape_source_auto", fake_scrape)
    monkeypatch.setattr(config, "SCRAPE_RETRY_BACKOFF_MAX", 0)

    # Patch backoff di modul run_indicator (sudah di-import).
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *_: None)

    job = _make_job()
    output = process_indicator_job(
        job,
        on_progress=lambda info: messages.append(info),
    )

    assert output["status"] == "SUCCESS"
    assert len(attempts_seen) == 3
    assert any("Percobaan 1/" in m["message"] for m in messages)
    assert any("Percobaan 3/" in m["message"] for m in messages)
    assert all("progress" in m for m in messages)


def test_process_indicator_job_stops_after_max_attempts(monkeypatch):
    """Setelah MAX_SCRAPE_ATTEMPTS gagal, tidak ada retry lagi."""
    from engine import run_indicator

    calls = []

    def always_fail(**kwargs):
        calls.append(1)
        raise RuntimeError("koneksi putus")

    monkeypatch.setattr(run_indicator, "scrape_source_auto", always_fail)

    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *_: None)

    job = _make_job()
    output = process_indicator_job(job)

    assert len(calls) == run_indicator.MAX_SCRAPE_ATTEMPTS
    assert output["status"] == "NEED_PARSER"

    summary = output["failure_summary"]
    assert "Data tahun 2024" in summary
    assert "tidak berhasil membaca data dari halaman sumber" in summary
    # Detail teknis exception tidak boleh muncul di ringkasan.
    assert "koneksi putus" not in summary
    assert "RuntimeError" not in summary


def test_process_indicator_job_no_retry_on_final_status(monkeypatch):
    """DATA_TIDAK_ADA tidak di-retry."""
    from engine import run_indicator

    calls = []

    def no_data(**kwargs):
        calls.append(1)
        return _FakeResult("DATA_TIDAK_ADA")

    monkeypatch.setattr(run_indicator, "scrape_source_auto", no_data)

    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *_: None)

    job = _make_job()
    output = process_indicator_job(job)

    assert len(calls) == 1
    assert output["status"] == "DATA_TIDAK_ADA"
    assert "sumber tidak menyediakan data" in output["failure_summary"]


def test_flip_pdf_province_summary_reads_years_from_page():
    """Tahun kolom ringkasan provinsi dibaca dari halaman, bukan hardcode."""
    page = (
        "tabel ringkasan irbi provinsi irbi 2024 irbi 2025 "
        "22 kalimantan selatan 129.44 122.11 turun"
    )
    assert (
        _province_summary_value(page, "Prov. Kalimantan Selatan", 2024)
        == 129.44
    )
    assert (
        _province_summary_value(page, "Prov. Kalimantan Selatan", 2025)
        == 122.11
    )
    assert (
        _province_summary_value(page, "Prov. Kalimantan Selatan", 2026)
        is None
    )


def test_build_failure_summary_friendly():
    """Ringkasan gagal: bahasa sederhana, sumber tak dipakai diabaikan."""
    diagnostics = [
        {
            "tahun": 2018,
            "source": 1,
            "status": "DATA_TIDAK_ADA",
            "message": "parse error detail",
        },
        {
            "tahun": 2018,
            "source": 2,
            "status": "NEED_PARSER",
            "message": "boom",
        },
        {
            "tahun": 2019,
            "source": 1,
            "status": "SKIPPED_EXISTING",
            "message": "",
        },
        {
            "tahun": 2020,
            "source": 1,
            "status": "SUCCESS",
            "message": "",
        },
        {
            "tahun": 2021,
            "source": 1,
            "status": "FALLBACK",
            "message": "",
        },
    ]
    regions = ["Prov. Kalimantan Selatan"] + [
        f"Wilayah {index}" for index in range(13)
    ]

    summary = _build_failure_summary(
        diagnostics,
        used_sources={1},
        regions=regions,
    )

    assert "Data tahun 2018 (untuk 14 wilayah)" in summary
    assert (
        "sumber 1: sumber tidak menyediakan data untuk tahun tersebut"
        in summary
    )
    # Kegagalan sumber 2 tidak dipakai Rumus S1 -> bukan kegagalan year.
    assert "boom" not in summary
    assert "parse error detail" not in summary
    # Sukses / skip / fallback bukan kegagalan.
    assert "2019" not in summary
    assert "2020" not in summary
    assert "2021" not in summary
    # Tidak ada istilah teknis.
    assert "DATA_TIDAK_ADA" not in summary
    assert "SKIPPED_EXISTING" not in summary

    single = _build_failure_summary(
        [{"tahun": 2021, "source": 1, "status": "NEED_PARSER"}],
        used_sources={1},
        regions=["Prov. Kalimantan Selatan"],
    )
    assert (
        "Data tahun 2021 (untuk wilayah Prov. Kalimantan Selatan)"
        in single
    )
    assert "tidak berhasil membaca data dari halaman sumber" in single

    formula = _build_failure_summary(
        [{"tahun": 2022, "status": "FAILED_FORMULA", "message": "S1 kosong"}],
        used_sources={1},
        regions=[],
    )
    assert "Data tahun 2022:" in formula
    assert "nilai tidak bisa dihitung" in formula
    assert "S1 kosong" not in formula

    # Tidak ada kegagalan -> ringkasan kosong.
    assert (
        _build_failure_summary(
            [{"tahun": 2018, "source": 1, "status": "SUCCESS"}],
            used_sources={1},
            regions=regions,
        )
        == ""
    )


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
    test_flip_pdf_province_summary_reads_years_from_page()
    test_build_failure_summary_friendly()
    test_retryable_statuses()
    try:
        import pytest  # noqa: F401
        # monkeypatch-based tests jalan via pytest.
    except ImportError:
        pass
    else:
        print("(jalankan monkeypatch tests via: pytest tests/test_fixes.py -q)")
    print("ALL TESTS PASSED")