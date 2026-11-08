import json

import requests

from models import ExtractionResult
from sources import bps_table_adapter


def test_metric_terms_exclude_scope_context_words():
    terms = bps_table_adapter._metric_terms(
        "Kode 2.d | Pertumbuhan Ekonomi (%) | Cakupan IDSD Kalsel",
        "Pertumbuhan Ekonomi (%)",
    )

    assert terms == ["persentase", "pertumbuhan", "ekonomi"]
    assert not {"cakupan", "idsd", "kalsel", "kalimantan", "selatan"} & set(terms)


def test_bps_browser_network_fallback_parses_next_flight_payload(monkeypatch):
    from extractors import browser

    payload = {
        "datacontent": {
            "6303 299 0 2025 0": "4,2",
        },
        "vervar": [
            {"val": 6303, "label": "BANJAR"},
        ],
        "var": [
            {"val": 299, "label": "Pertumbuhan Ekonomi"},
        ],
        "turvar": [
            {"val": 0, "label": "Tidak ada"},
        ],
        "tahun": [
            {"val": 2025, "label": "2025"},
        ],
        "turtahun": [
            {"val": 0, "label": "Tahun"},
        ],
    }
    response_body = (
        "1:" + json.dumps(payload, ensure_ascii=False) + "\n"
    ).encode("utf-8")

    monkeypatch.setattr(
        browser,
        "render_page_capture_network",
        lambda *_args, **_kwargs: (
            "<html><body>Dynamic BPS table</body></html>",
            [
                (
                    "https://kalsel.bps.go.id/id/statistics-table/2/table",
                    response_body,
                    "text/x-component",
                )
            ],
            b"",
        ),
    )

    result = bps_table_adapter._extract_bps_browser_fallback(
        url=(
            "https://kalsel.bps.go.id/id/statistics-table/2/Mjk5IzI/"
            "pertumbuhan-ekonomi.html"
        ),
        tahun=2025,
        description="Cakupan IDSD Kalsel",
        wilayah="Kab. Banjar",
        nama_indikator="Pertumbuhan Ekonomi (%)",
    )

    assert result.status == "SUCCESS"
    assert result.source_type == "bps_table_browser_network"
    assert result.value == {"Kab. Banjar": 4.2}


def test_bps_download_error_uses_browser_fallback(monkeypatch):
    monkeypatch.setattr(bps_table_adapter, "_CACHE", {})
    monkeypatch.setattr(
        bps_table_adapter,
        "download_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            requests.HTTPError("525 Server Error")
        ),
    )

    expected = ExtractionResult(
        status="SUCCESS",
        value={"Prov. Kalimantan Selatan": 305126.93},
        confidence=0.96,
        source_type="bps_table_browser_network",
    )
    calls = {}

    def fake_browser(url, tahun, **kwargs):
        calls.update(url=url, tahun=tahun, **kwargs)
        return expected

    monkeypatch.setattr(
        bps_table_adapter,
        "_extract_bps_browser_fallback",
        fake_browser,
    )

    result = bps_table_adapter.try_bps_table(
        url=(
            "https://kalsel.bps.go.id/id/statistics-table/3/abc/"
            "pdrb-adhb.html"
        ),
        tahun=2023,
        description="PDRB atas dasar harga berlaku",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Aset Dana Pensiun/PDRB (%)",
    )

    assert result is expected
    assert calls["tahun"] == 2023
    assert calls["wilayah"] == "Prov. Kalimantan Selatan"


def test_browser_capture_accepts_next_flight_content_type():
    from extractors.browser import _is_supported_network_response

    assert _is_supported_network_response(
        "Fetch",
        "text/x-component; charset=utf-8",
        "https://kalsel.bps.go.id/id/statistics-table/2/table",
    )
    assert not _is_supported_network_response(
        "Document",
        "text/html",
        "https://kalsel.bps.go.id/id/statistics-table/2/table",
    )


def test_router_tries_bps_adapter_when_initial_fetch_fails(monkeypatch):
    from engine import router

    expected = ExtractionResult(
        status="SUCCESS",
        value={"Kab. Banjar": 4.2},
        confidence=0.96,
        source_type="bps_table_browser_network",
    )
    calls = {}

    def fail_fetch(_url):
        raise requests.HTTPError("525 Server Error")

    def fake_bps(**kwargs):
        calls.update(kwargs)
        return expected

    monkeypatch.setattr(router, "fetch_and_detect", fail_fetch)
    monkeypatch.setattr(
        bps_table_adapter,
        "try_bps_table",
        fake_bps,
    )

    result = router.scrape_source_auto(
        url=(
            "https://kalsel.bps.go.id/id/statistics-table/2/Mjk5IzI/"
            "pertumbuhan-ekonomi.html"
        ),
        tahun=2025,
        description="Cakupan IDSD Kalsel",
        wilayah="Kab. Banjar",
        kode="2.d",
        nama_indikator="Pertumbuhan Ekonomi (%)",
    )

    assert result.status == "SUCCESS"
    assert calls["tahun"] == 2025
    assert calls["nama_indikator"] == "Pertumbuhan Ekonomi (%)"
