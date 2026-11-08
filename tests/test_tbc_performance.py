"""Regression tests for universal TBC source-bundle performance fixes."""

import time
from concurrent.futures import ThreadPoolExecutor

from models import ExtractionResult
from sources import tbc_source
from sources.registry import AdapterContext, matching_adapter_spec


OPENDATA_URL = (
    "https://opendata.kalselprov.go.id/open-data/dataset/"
    "b43f5eef-565f-43f8-9f80-0aa9e0c079eb"
)


def test_tbc_adapter_declares_source_bundle_and_direct_route():
    ctx = AdapterContext(
        url=OPENDATA_URL,
        tahun=2024,
        description=(
            "Kode 3.a | Cakupan penemuan kasus Tuberkulosis | "
            "kab/kota Kalimantan Selatan"
        ),
        nama_indikator="Cakupan penemuan kasus Tuberkulosis(%)",
        sumber_2="https://example.test/profil-2023.pdf",
        sumber_3="https://example.test/profil-2024.pdf",
    )

    spec = matching_adapter_spec(ctx)

    assert spec is not None
    assert spec.name == "tbc"
    assert spec.handles_source_bundle is True
    assert spec.prefer_direct is True


def test_tbc_all_data_cache_is_single_flight_under_concurrency(monkeypatch):
    # Isolasi cache dari test lain.
    with tbc_source._TBC_CACHE_LOCK:
        tbc_source._ALL_DATA_CACHE.clear()

    calls = {"count": 0}

    def fake_build_all_data(source_urls):
        calls["count"] += 1
        time.sleep(0.05)
        return {
            "Prov. Kalimantan Selatan": {
                2024: 75.5,
            }
        }

    monkeypatch.setattr(
        tbc_source,
        "_build_all_data",
        fake_build_all_data,
    )

    def scrape_once(_):
        return tbc_source.scrape_tbc(
            url=OPENDATA_URL,
            tahun=2024,
            description=(
                "Cakupan penemuan kasus Tuberkulosis "
                "Provinsi Kalimantan Selatan"
            ),
            wilayah="Prov. Kalimantan Selatan",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(scrape_once, range(8)))

    assert results == [75.5] * 8
    assert calls["count"] == 1


def test_direct_tbc_route_skips_generic_page_fetch(monkeypatch):
    import pytest
    pytest.importorskip("selenium")
    import engine.router as router

    calls = {"adapter": 0}

    def forbidden_fetch(*args, **kwargs):
        raise AssertionError(
            "fetch_and_detect tidak boleh dipanggil untuk adapter direct TBC"
        )

    def fake_legacy(**kwargs):
        calls["adapter"] += 1
        return ExtractionResult(
            status="SUCCESS",
            value={"Kab. Tanah Laut": 75.5},
            confidence=1.0,
            source_type="legacy:tbc",
            message="fixture",
        )

    monkeypatch.setattr(router, "fetch_and_detect", forbidden_fetch)
    monkeypatch.setattr(router, "try_legacy_source", fake_legacy)

    result = router.scrape_source_auto(
        url=OPENDATA_URL,
        tahun=2024,
        description=(
            "Kode 3.a | Cakupan penemuan kasus Tuberkulosis | "
            "kab/kota Kalimantan Selatan"
        ),
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Cakupan penemuan kasus Tuberkulosis(%)",
        sumber_2="",
        sumber_3="",
    )

    assert result.status == "SUCCESS"
    assert calls["adapter"] == 1
