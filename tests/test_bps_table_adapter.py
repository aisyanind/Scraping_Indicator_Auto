import json

from models import ExtractionResult
from sources import bps_table_adapter
from sources.bps_utils import extract_valid_metric_terms


def test_statistics_table_listing_url_is_distinguished_from_detail_url():
    assert bps_table_adapter._is_bps_statistics_table_listing(
        "https://kalsel.bps.go.id/id/statistics-table?subject=531"
    )
    assert bps_table_adapter._is_bps_statistics_table_listing(
        "https://kalsel.bps.go.id/id/statistics-table/?subject=531"
    )
    assert not bps_table_adapter._is_bps_statistics_table_listing(
        "https://kalsel.bps.go.id/id/statistics-table/1/123/title.html"
    )
    assert not bps_table_adapter._is_bps_statistics_table_listing(
        "https://example.com/id/statistics-table?subject=531"
    )


def test_detail_adapter_matches_provincial_aggregate_row_from_title(monkeypatch):
    table = {
        "judul": (
            "Produk Domestik Regional Bruto Menurut Lapangan Usaha "
            "di Provinsi Kalimantan Selatan (miliar rupiah), 2025"
        ),
        "tahun_data": 2025,
        "list_tahun": [2025],
        "kolom": {
            "pdrb": {
                "nama_variabel": (
                    "Produk Domestik Regional Bruto Atas Dasar Harga Berlaku"
                ),
            },
        },
        "rows": [
            {
                "label": "Pertanian, Kehutanan, dan Perikanan",
                "variables": {
                    "pdrb": {"value_raw": "42.000,00"},
                },
            },
            {
                "label": (
                    "Produk Domestik Regional Bruto<br>"
                    "<i>Gross Regional Domestic Product</i>"
                ),
                "variables": {
                    "pdrb": {"value_raw": "305.126,93"},
                },
            },
        ],
    }
    monkeypatch.setattr(bps_table_adapter, "_CACHE", {})
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda *_args, **_kwargs: "<html>table</html>",
    )
    monkeypatch.setattr(
        bps_table_adapter,
        "parse_bps_statistics_table",
        lambda _html: table,
    )

    result = bps_table_adapter.try_bps_table(
        url=(
            "https://kalsel.bps.go.id/id/statistics-table/3/123/"
            "pdrb-menurut-lapangan-usaha.html"
        ),
        tahun=2025,
        description="Total Dana Pihak Ketiga/PDRB (%)",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Total Dana Pihak Ketiga/PDRB (%)",
    )

    assert result.status == "SUCCESS"
    assert result.value == {"Prov. Kalimantan Selatan": 305126.93}


def test_detail_adapter_selects_requested_industry_row_not_total_pdrb(
    monkeypatch,
):
    table = {
        "judul": (
            "Produk Domestik Regional Bruto Atas Dasar Harga Berlaku "
            "Menurut Lapangan Usaha di Provinsi Kalimantan Selatan, 2025"
        ),
        "tahun_data": 2025,
        "list_tahun": [2025],
        "kolom": {
            "pdrb": {
                "nama_variabel": (
                    "Produk Domestik Regional Bruto Atas Dasar Harga Berlaku"
                ),
            },
        },
        "rows": [
            {
                "label": "C Industri Pengolahan/ Manufacturing",
                "variables": {
                    "pdrb": {"value_raw": "35.441,11"},
                },
            },
            {
                "label": (
                    "Produk Domestik Regional Bruto<br>"
                    "<i>Gross Regional Domestic Product</i>"
                ),
                "variables": {
                    "pdrb": {"value_raw": "305.126,93"},
                },
            },
        ],
    }
    monkeypatch.setattr(bps_table_adapter, "_CACHE", {})
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda *_args, **_kwargs: "<html>table</html>",
    )
    monkeypatch.setattr(
        bps_table_adapter,
        "parse_bps_statistics_table",
        lambda _html: table,
    )

    result = bps_table_adapter.try_bps_table(
        url=(
            "https://kalsel.bps.go.id/id/statistics-table/3/123/"
            "pdrb-menurut-lapangan-usaha.html"
        ),
        tahun=2025,
        description="Kontribusi PDRB Industri Pengolahan (%)",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Kontribusi PDRB Industri Pengolahan (%)",
    )

    assert result.status == "SUCCESS"
    assert result.value == {"Prov. Kalimantan Selatan": 35441.11}


def test_provincial_aggregate_row_requires_matching_title_and_no_region_codes():
    table = {
        "judul": "PDRB di Provinsi Sulawesi Selatan, 2025",
        "rows": [
            {
                "label": "Produk Domestik Regional Bruto",
                "variables": {"pdrb": {"value_raw": "1.234,00"}},
            },
        ],
    }

    assert bps_table_adapter._match_provincial_aggregate_row(
        table,
        "pdrb",
        ["Prov. Kalimantan Selatan"],
    ) == {}

    table["judul"] = "PDRB di Provinsi Kalimantan Selatan, 2025"
    assert bps_table_adapter._match_provincial_aggregate_row(
        table,
        "pdrb",
        ["Prov. Kalimantan Selatan"],
        ["pdrb", "industri", "pengolahan"],
    ) == {}

    table["rows"][0]["kode_wilayah"] = 6300000
    assert bps_table_adapter._match_provincial_aggregate_row(
        table,
        "pdrb",
        ["Prov. Kalimantan Selatan"],
    ) == {}


def test_catalog_search_uses_indicator_name_and_ranks_matching_title():
    terms = bps_table_adapter._table_search_terms(
        "Ambil data dari tahun terbaru",
        nama_indikator="Pertumbuhan Ekonomi",
    )

    assert terms == ["pertumbuhan", "ekonomi"]
    assert bps_table_adapter._table_title_score(
        "Pertumbuhan Ekonomi Produk Domestik Regional Bruto",
        terms,
    ) > bps_table_adapter._table_title_score(
        "Jumlah Penduduk Menurut Kabupaten",
        terms,
    )


def test_catalog_detail_lookup_uses_indicator_title(monkeypatch):
    catalog_url = (
        "https://kalsel.bps.go.id/id/statistics-table?subject=531"
    )
    html = """
    <a href="/id/statistics-table/1/100/jumlah-penduduk.html">
      Jumlah Penduduk
    </a>
    <a href="/id/statistics-table/1/123/pertumbuhan-ekonomi.html">
      Pertumbuhan Ekonomi
    </a>
    """
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda url, **_kwargs: html if url == catalog_url else "",
    )

    detail_url, _catalog_html, evidence = (
        bps_table_adapter._find_catalog_detail_url(
            catalog_url,
            "Ambil data terbaru",
            nama_indikator="Pertumbuhan Ekonomi",
        )
    )

    assert detail_url == (
        "https://kalsel.bps.go.id/id/statistics-table/1/123/"
        "pertumbuhan-ekonomi.html"
    )
    assert evidence == ["catalog_rows_seen=1"]


def test_metric_terms_keep_indicator_words_when_name_has_percent():
    terms = bps_table_adapter._metric_terms(
        "Kode 2.d | Pertumbuhan Ekonomi (%)",
        "Pertumbuhan Ekonomi (%)",
    )

    assert "persentase" in terms
    assert "pertumbuhan" in terms
    assert "ekonomi" in terms
    assert bps_table_adapter._within_metric_range(-2.1, terms)
    assert {"persentase", "pertumbuhan", "ekonomi"} <= set(
        bps_table_adapter.extract_metric_terms(
            "Pertumbuhan Ekonomi (%)"
        )
    )


def test_poverty_rate_catalog_search_excludes_poverty_line_table(monkeypatch):
    catalog_url = (
        "https://kalsel.bps.go.id/id/statistics-table?subject=531"
    )
    expected_detail_url = (
        "https://kalsel.bps.go.id/id/statistics-table/1/123/"
        "persentase-penduduk-miskin.html"
    )
    html = """
    <a href="/id/statistics-table/1/100/garis-kemiskinan.html">
      Garis Kemiskinan - Maret
    </a>
    <a href="/id/statistics-table/1/123/persentase-penduduk-miskin.html">
      Persentase Penduduk Miskin (P0) Menurut Kabupaten/Kota
    </a>
    """
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda *_args, **_kwargs: html,
    )

    detail_url, _html, _evidence = (
        bps_table_adapter._find_catalog_detail_url(
            catalog_url,
            "Tingkat Kemiskinan (%)",
            nama_indikator="Tingkat Kemiskinan (%)",
        )
    )

    assert detail_url == expected_detail_url


def test_poverty_rate_does_not_select_poverty_line_column():
    table = {
        "kolom": {
            "poverty_line": {
                "nama_variabel": "Garis Kemiskinan - Maret",
            },
        },
    }

    assert bps_table_adapter._pick_column(
        table,
        ["persentase", "kemiskinan", "tingkat"],
        "Tingkat Kemiskinan (%)",
    ) == (None, None)


def test_poverty_rate_without_percent_sign_selects_percentage_column():
    table = {
        "kolom": {
            "count": {
                "nama_variabel": (
                    "Jumlah Penduduk Miskin - Maret (ribu)"
                ),
            },
            "rate": {
                "nama_variabel": "Persentase Penduduk Miskin - Maret",
            },
        },
    }
    terms = bps_table_adapter._metric_terms(
        "[8] Tingkat Kemiskinan",
        "Tingkat Kemiskinan",
    )

    assert "persentase" in terms
    assert bps_table_adapter._pick_column(
        table,
        terms,
        "[8] Tingkat Kemiskinan",
    ) == ("rate", table["kolom"]["rate"])


def test_poverty_count_indicator_still_selects_count_column():
    table = {
        "kolom": {
            "count": {
                "nama_variabel": (
                    "Jumlah Penduduk Miskin - Maret (ribu)"
                ),
            },
            "rate": {
                "nama_variabel": "Persentase Penduduk Miskin - Maret",
            },
        },
    }
    terms = bps_table_adapter._metric_terms(
        "Jumlah Penduduk Miskin",
        "Jumlah Penduduk Miskin",
    )

    assert bps_table_adapter._pick_column(
        table,
        terms,
        "Jumlah Penduduk Miskin",
    ) == ("count", table["kolom"]["count"])


def test_poverty_rate_adapter_rejects_poverty_line_result(monkeypatch):
    table = {
        "judul": "Garis Kemiskinan Menurut Kabupaten/Kota, 2025",
        "tahun_data": 2025,
        "list_tahun": [2025],
        "kolom": {
            "poverty_line": {
                "nama_variabel": "Garis Kemiskinan - Maret",
            },
        },
        "rows": [
            {
                "label": "Kab. Banjar",
                "variables": {
                    "poverty_line": {"value_raw": "650.675"},
                },
            },
        ],
    }
    monkeypatch.setattr(bps_table_adapter, "_CACHE", {})
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda *_args, **_kwargs: "<html>table</html>",
    )
    monkeypatch.setattr(
        bps_table_adapter,
        "parse_bps_statistics_table",
        lambda _html: table,
    )

    result = bps_table_adapter.try_bps_table(
        url=(
            "https://kalsel.bps.go.id/id/statistics-table/1/100/"
            "garis-kemiskinan.html"
        ),
        tahun=2025,
        description="Tingkat Kemiskinan (%)",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Tingkat Kemiskinan (%)",
    )

    assert result.status == "NEED_PARSER"
    assert result.value is None
    assert "bukan persentase penduduk miskin" in result.message


def test_metric_matching_does_not_match_synonym_inside_another_word():
    description = "Indeks Ketimpangan Gender (IKG)"

    assert "pangan" not in bps_table_adapter.extract_metric_terms(
        description
    )
    assert extract_valid_metric_terms(description) == ["indeks"]


def test_rsc_extraction_matches_variable_year_and_returns_region_mapping():
    table_data = {
        "datacontent": {
            "1 5 0 2025": "4,2",
            "2 5 0 2025": "3,8",
            "1 6 0 2025": "1000",
            "1 5 0 2024": "3,5",
        },
        "vervar": [
            {"val": 1, "label": "Kabupaten Banjar"},
            {"val": 2, "label": "Kota Banjarbaru"},
        ],
        "var": [
            {"val": 5, "label": "Laju Pertumbuhan Ekonomi"},
            {"val": 6, "label": "Jumlah Penduduk"},
        ],
        "tahun": [{"val": 2025, "label": "2025"}, {"val": 2024, "label": "2024"}],
    }

    values = bps_table_adapter._pick_best_from_rsc(
        table_data,
        ["persentase", "pertumbuhan", "ekonomi"],
        ["Kab. Banjar", "Kota Banjarbaru"],
        2025,
    )

    assert values == {
        "Kab. Banjar": 4.2,
        "Kota Banjarbaru": 3.8,
    }


def test_detail_adapter_matches_growth_metric_and_negative_values(monkeypatch):
    table_data = {
        "datacontent": {
            "1 5 0 2020": "-1,8",
            "1 6 0 2020": "1000",
        },
        "vervar": [
            {"val": 1, "label": "Kalimantan Selatan"},
        ],
        "var": [
            {"val": 5, "label": "Laju Pertumbuhan Ekonomi"},
            {"val": 6, "label": "Jumlah Penduduk"},
        ],
        "tahun": [{"val": 2020, "label": "2020"}],
    }
    monkeypatch.setattr(bps_table_adapter, "_CACHE", {})
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda *_args, **_kwargs: "<html>data</html>",
    )
    monkeypatch.setattr(
        bps_table_adapter,
        "_extract_from_rsc_payload",
        lambda _html: table_data,
    )

    result = bps_table_adapter.try_bps_table(
        url=(
            "https://kalsel.bps.go.id/id/statistics-table/1/123/"
            "pertumbuhan-ekonomi.html"
        ),
        tahun=2020,
        description="Kode 2.d | Pertumbuhan Ekonomi (%)",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Pertumbuhan Ekonomi (%)",
    )

    assert result.status == "SUCCESS"
    assert result.value == {"Prov. Kalimantan Selatan": -1.8}


def test_detail_adapter_returns_region_mapping_not_main_placeholder(monkeypatch):
    table_data = {
        "datacontent": {
            "1 5 0 2025": "4,2",
            "2 5 0 2025": "3,8",
        },
        "vervar": [
            {"val": 1, "label": "Kabupaten Banjar"},
            {"val": 2, "label": "Kota Banjarbaru"},
        ],
        "var": [{"val": 5, "label": "Laju Pertumbuhan Ekonomi"}],
        "tahun": [{"val": 2025, "label": "2025"}],
    }
    monkeypatch.setattr(bps_table_adapter, "_CACHE", {})

    def fake_download(url, timeout=60, tag=""):
        assert "/statistics-table/1/123/" in url
        assert timeout > 0
        assert tag == "BPS-TABLE"
        return "<html>table</html>"

    def fake_extract_payload(html):
        assert html == "<html>table</html>"
        return table_data

    monkeypatch.setattr(bps_table_adapter, "download_html", fake_download)
    monkeypatch.setattr(
        bps_table_adapter,
        "_extract_from_rsc_payload",
        fake_extract_payload,
    )

    result = bps_table_adapter._extract_bps_table_detail(
        "https://kalsel.bps.go.id/id/statistics-table/1/123/table.html",
        2025,
        description=(
            "Kode 2.d | Pertumbuhan Ekonomi (%) | seluruh kabupaten/kota"
        ),
        wilayah="Provinsi Kalimantan Selatan",
        nama_indikator="Pertumbuhan Ekonomi (%)",
    )

    assert result.status == "SUCCESS"
    assert result.value == {
        "Kab. Banjar": 4.2,
        "Kota Banjarbaru": 3.8,
    }
    assert "__main__" not in result.value


def test_structured_table_preserves_negative_growth_values():
    table = {
        "rows": [
            {
                "label": "Kalimantan Selatan",
                "variables": {
                    "growth": {"value_raw": "-1,8"},
                },
            },
        ],
    }

    assert bps_table_adapter._match_rows(
        table,
        "growth",
        ["Prov. Kalimantan Selatan"],
    ) == {"Prov. Kalimantan Selatan": -1.8}


def test_row_value_does_not_fall_back_to_an_unselected_variable():
    row = {
        "variables": {
            "first": {"value_raw": "6,71"},
        },
    }

    assert bps_table_adapter._row_value(row, "missing") is None


def test_detail_adapter_extracts_plain_html_table(monkeypatch):
    html = """
    <table>
      <thead>
        <tr><th>Wilayah</th><th>Pertumbuhan Ekonomi 2025</th></tr>
      </thead>
      <tbody>
        <tr><td>Kabupaten Banjar</td><td>4,2</td></tr>
        <tr><td>Kota Banjarbaru</td><td>3,8</td></tr>
      </tbody>
    </table>
    """
    monkeypatch.setattr(bps_table_adapter, "_CACHE", {})
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda *_args, **_kwargs: html,
    )
    monkeypatch.setattr(
        bps_table_adapter,
        "_extract_from_rsc_payload",
        lambda _html: None,
    )

    result = bps_table_adapter._extract_bps_table_detail(
        "https://kalsel.bps.go.id/id/statistics-table/1/123/table.html",
        2025,
        description=(
            "Kode 2.d | Pertumbuhan Ekonomi (%) | seluruh kabupaten/kota"
        ),
        wilayah="Provinsi Kalimantan Selatan",
        nama_indikator="Pertumbuhan Ekonomi (%)",
    )

    assert result.status == "SUCCESS"
    assert result.source_type == "bps_table_html"
    assert result.value == {
        "Kab. Banjar": 4.2,
        "Kota Banjarbaru": 3.8,
    }


def test_catalog_adapter_extracts_selected_detail_page(monkeypatch):
    detail_url = (
        "https://kalsel.bps.go.id/id/statistics-table/1/123/"
        "pertumbuhan-ekonomi.html"
    )
    selected = {}
    selected_catalog = {}

    def fake_find_catalog_detail_url(url, description, nama_indikator):
        selected_catalog.update(
            url=url,
            description=description,
            nama_indikator=nama_indikator,
        )
        return detail_url, None, ["catalog_rows_seen=1"]

    monkeypatch.setattr(
        bps_table_adapter,
        "_find_catalog_detail_url",
        fake_find_catalog_detail_url,
    )

    def fake_extract(
        url,
        tahun,
        description=None,
        wilayah=None,
        nama_indikator=None,
    ):
        selected.update(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
            nama_indikator=nama_indikator,
        )
        return ExtractionResult(
            status="SUCCESS",
            value={"__main__": 4.2},
            confidence=0.9,
            source_type="bps_table_rsc",
        )

    monkeypatch.setattr(
        bps_table_adapter,
        "_extract_bps_table_detail",
        fake_extract,
    )

    result = bps_table_adapter.try_bps_table(
        url="https://kalsel.bps.go.id/id/statistics-table?subject=531",
        tahun=2024,
        description="Ambil data terbaru",
        wilayah="Provinsi Kalimantan Selatan",
        nama_indikator="Pertumbuhan Ekonomi",
    )

    assert selected == {
        "url": detail_url,
        "tahun": 2024,
        "description": "Ambil data terbaru",
        "wilayah": "Provinsi Kalimantan Selatan",
        "nama_indikator": "Pertumbuhan Ekonomi",
    }
    assert selected_catalog == {
        "url": "https://kalsel.bps.go.id/id/statistics-table?subject=531",
        "description": "Ambil data terbaru",
        "nama_indikator": "Pertumbuhan Ekonomi",
    }
    assert result.status == "SUCCESS"
    assert "catalog_url=https://kalsel.bps.go.id/id/statistics-table?subject=531" in result.evidence
    assert f"table_url={detail_url}" in result.evidence


def test_catalog_adapter_passes_indicator_to_detail_extractor(monkeypatch):
    detail_url = (
        "https://kalsel.bps.go.id/id/statistics-table/1/123/"
        "pertumbuhan-ekonomi.html"
    )
    detail_args = {}

    def fake_find_detail(url, description, nama_indikator):
        assert url.endswith("/statistics-table?subject=531")
        assert description
        assert "Pertumbuhan" in nama_indikator
        return detail_url, None, ["catalog_rows_seen=1"]

    monkeypatch.setattr(
        bps_table_adapter,
        "_find_catalog_detail_url",
        fake_find_detail,
    )

    def fake_extract(url, tahun, description=None, wilayah=None, nama_indikator=None):
        detail_args.update(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
            nama_indikator=nama_indikator,
        )
        return ExtractionResult(
            status="SUCCESS",
            value={"Kab. Banjar": 4.2},
            confidence=0.9,
            source_type="bps_table_rsc",
        )

    monkeypatch.setattr(
        bps_table_adapter,
        "_extract_bps_table_detail",
        fake_extract,
    )
    result = bps_table_adapter.try_bps_table(
        url="https://kalsel.bps.go.id/id/statistics-table?subject=531",
        tahun=2025,
        description="Kode 2.d | Pertumbuhan Ekonomi (%)",
        wilayah="Kab. Banjar",
        nama_indikator="Pertumbuhan Ekonomi (%)",
    )

    assert detail_args["nama_indikator"] == "Pertumbuhan Ekonomi (%)"
    assert result.value == {"Kab. Banjar": 4.2}


def test_router_routes_statistics_table_catalog_with_indicator_name(monkeypatch):
    from engine import router
    from engine.detector import SourcePayload

    calls = {}
    table_result = ExtractionResult(
        status="SUCCESS",
        value={"__main__": 4.2},
        confidence=0.95,
        source_type="bps_table",
    )

    def fake_legacy_source(**kwargs):
        calls["legacy_kwargs"] = kwargs
        return None

    def fake_fetch(url):
        calls["fetch_url"] = url
        return SourcePayload(
            url="https://kalsel.bps.go.id/id/statistics-table?subject=531",
            final_url="https://kalsel.bps.go.id/id/statistics-table?subject=531",
            kind="html",
            content=b"<html>Daftar tabel</html>",
            content_type="text/html",
        )

    def no_embedded_json(payload, instruction, tahun):
        calls["embedded_json_checked"] = True
        calls.setdefault(
            "json_context",
            (payload, instruction, tahun),
        )
        return None

    monkeypatch.setattr(router, "try_legacy_source", fake_legacy_source)
    monkeypatch.setattr(
        router,
        "fetch_and_detect",
        fake_fetch,
    )
    monkeypatch.setattr(router, "_try_bps_embedded_json", no_embedded_json)
    monkeypatch.setattr(router, "_try_bps_next_data", no_embedded_json)

    def fake_try_bps_table(
        url,
        tahun,
        description=None,
        wilayah=None,
        nama_indikator=None,
    ):
        calls.update(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
            nama_indikator=nama_indikator,
        )
        return table_result

    monkeypatch.setattr(bps_table_adapter, "try_bps_table", fake_try_bps_table)

    result = router.scrape_source_auto(
        url="https://kalsel.bps.go.id/id/statistics-table?subject=531",
        tahun=2024,
        description="Ambil data indikator",
        wilayah="Provinsi Kalimantan Selatan",
        nama_indikator="Pertumbuhan Ekonomi",
    )

    assert result is table_result
    assert calls["nama_indikator"] == "Pertumbuhan Ekonomi"


def test_bps_action_id_is_discovered_from_next_chunk_manifest(monkeypatch):
    webpack_url = (
        "https://kalsel.bps.go.id/_next/static/chunks/webpack-test.js"
    )
    chunk_url = (
        "https://kalsel.bps.go.id/_next/static/chunks/"
        "1001.aaaaaaaaaaaaaaaa.js"
    )
    action_id = "a" * 40

    class FakeResponse:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(url, **_kwargs):
        if url == webpack_url:
            return FakeResponse(
                'r.u=e=>({1001:"aaaaaaaaaaaaaaaa"})[e]+".js",r.miniCssF'
            )
        if url == chunk_url:
            return FakeResponse(
                'createServerReference)("'
                + action_id
                + '",r.callServer,void 0,r.findSourceMapURL,"getDataTable")'
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(bps_table_adapter.requests, "get", fake_get)

    assert bps_table_adapter._discover_bps_table_action_id(
        "https://kalsel.bps.go.id/id/statistics-table/2/table/title.html",
        f'<script src="{webpack_url}"></script>',
    ) == action_id


def test_router_uses_rendered_bps_table_after_action_parser_failure(
    monkeypatch,
):
    from engine import router
    from engine.detector import SourcePayload

    url = (
        "https://kalsel.bps.go.id/id/statistics-table/2/table/"
        "indeks-demokrasi.html"
    )
    table_error = ExtractionResult(
        status="NEED_PARSER",
        source_type="bps_table_action",
        message="Referensi aksi getDataTable tidak ditemukan.",
    )
    browser_result = ExtractionResult(
        status="SUCCESS",
        value=81.23,
        confidence=0.9,
        source_type="browser",
    )
    browser_calls = {}
    monkeypatch.setattr(
        router,
        "fetch_and_detect",
        lambda _url: SourcePayload(
            url=url,
            kind="spa",
            content=b"<html></html>",
            content_type="text/html",
            final_url=url,
        ),
    )
    monkeypatch.setattr(
        bps_table_adapter,
        "try_bps_table",
        lambda **_kwargs: table_error,
    )
    monkeypatch.setattr(
        router,
        "_try_bps_embedded_json",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        router,
        "_try_bps_next_data",
        lambda *_args: None,
    )

    def fake_extract_browser(
        browser_url,
        instruction,
        year,
        **number_format,
    ):
        browser_calls.update(
            url=browser_url,
            year=year,
            number_format=number_format,
        )
        return browser_result

    monkeypatch.setattr(
        router,
        "extract_browser",
        fake_extract_browser,
    )

    result = router.scrape_source_auto(
        url=url,
        tahun=2025,
        description="Indeks Demokrasi Indonesia (IDI)",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Indeks Demokrasi Indonesia (IDI)",
    )

    assert result is browser_result
    assert browser_calls == {
        "url": url,
        "year": 2025,
        "number_format": {
            "decimal": ",",
            "thousands": ".",
            "select_year": 2025,
        },
    }


def test_rsc_combined_keys_are_decoded_with_year_and_dimension_ids():
    data = {
        "datacontent": {"630329901190": "4,2"},
        "vervar": [{"val": 6303, "label": "BANJAR"}],
        "var": [{"val": 299, "label": "Pertumbuhan Ekonomi"}],
        "turvar": [{"val": 0, "label": "Tidak ada"}],
        "tahun": [{"val": 119, "label": "2019"}],
        "turtahun": [{"val": 0, "label": "Tahun"}],
    }

    assert bps_table_adapter._pick_regions_from_rsc(
        data,
        ["pertumbuhan", "ekonomi"],
        ["Kab. Banjar"],
        2019,
    ) == {"Kab. Banjar": 4.2}


def test_detail_adapter_selects_requested_year_and_returns_all_regions(
    monkeypatch,
):
    url = (
        "https://kalsel.bps.go.id/id/statistics-table/2/Mjk5IzI=/"
        "pertumbuhan-ekonomi-menurut-kabupaten-kota-di-provinsi-"
        "kalimantan-selatan--persen-.html"
    )
    page_data = {
        "listTahun": [{"th_id": 124, "th": "2024"}],
    }
    html = (
        "<script>self.__next_f.push([1,"
        + json.dumps(json.dumps(page_data))
        + "])</script>"
    )
    region_ids = [
        ("6300", "KALIMANTAN SELATAN"),
        ("6301", "TANAH LAUT"),
        ("6302", "KOTABARU"),
        ("6303", "BANJAR"),
        ("6304", "BARITO KUALA"),
        ("6305", "TAPIN"),
        ("6306", "HULU SUNGAI SELATAN"),
        ("6307", "HULU SUNGAI TENGAH"),
        ("6308", "HULU SUNGAI UTARA"),
        ("6309", "TABALONG"),
        ("6310", "TANAH BUMBU"),
        ("6311", "BALANGAN"),
        ("6371", "KOTA BANJARMASIN"),
        ("6372", "KOTA BANJAR BARU"),
    ]
    data = {
        "status": "OK",
        "data-availability": "available",
        "vervar": [
            {"val": int(region_id), "label": label}
            for region_id, label in region_ids
        ],
        "var": [{"val": 299, "label": "Pertumbuhan Ekonomi", "unit": "Persen"}],
        "turvar": [{"val": 0, "label": "Tidak ada"}],
        "tahun": [{"val": 124, "label": "2024"}],
        "turtahun": [{"val": 0, "label": "Tahun"}],
        "datacontent": {
            f"{region_id}29901240": "5,06"
            for region_id, _label in region_ids
        },
    }
    body = "\n".join(
        (
            '0:{"a":"$@1","f":"","b":"test"}',
            "1:" + json.dumps(
                {"status": True, "response": data},
                separators=(",", ":"),
            ),
        )
    )

    class FakeResponse:
        status_code = 200
        text = body

        def raise_for_status(self):
            return None

    post_request = {}

    def fake_post(request_url, headers, data, timeout):
        post_request.update(
            url=request_url,
            headers=headers,
            body=json.loads(data),
            timeout=timeout,
        )
        return FakeResponse()

    monkeypatch.setattr(bps_table_adapter, "_CACHE", {})
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda *_args, **_kwargs: html,
    )
    monkeypatch.setattr(
        bps_table_adapter,
        "_discover_bps_table_action_id",
        lambda *_args, **_kwargs: "b" * 40,
    )
    monkeypatch.setattr(
        bps_table_adapter.requests,
        "post",
        fake_post,
    )

    result = bps_table_adapter.try_bps_table(
        url=url,
        tahun=2024,
        description=(
            "Kode 2.d | Pertumbuhan Ekonomi (%) | seluruh kabupaten/kota"
        ),
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Pertumbuhan Ekonomi (%)",
    )

    assert result.status == "SUCCESS"
    assert result.source_type == "bps_table_action"
    assert len(result.value) == 14
    assert result.value["Prov. Kalimantan Selatan"] == 5.06
    assert post_request["url"] == url
    assert post_request["body"] == [
        {"locale": "id", "id": "Mjk5IzI=", "year": "124"},
    ]
    assert post_request["headers"]["Next-Action"] == "b" * 40
