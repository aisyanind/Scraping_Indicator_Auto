"""
Regresi fix jalur Google Drive -> PDF -> grafik gender:

1. URL share Google Drive di-resolve ke endpoint unduhan langsung
   supaya payload kind=pdf (bukan halaman viewer HTML).
2. Frame dengan sel region gabungan (multi-nama) ditolak — value
   digit kolom NO tidak boleh diterima sebagai angka raksasa.
3. Sinonim tbc/tuberkulosis di METRIC_SYNONYMS.
4. Adapter registry `tbc` menolak PDF-only untuk indikator kab/kota.
5. Test jaringan live: URL share + nama indikator asli -> SUCCESS 13/13.
"""

import re

import pandas as pd
import pytest
import requests

from config import KALSEL_WILAYAHS
from engine.detector import _resolve_gdrive_url, fetch_and_detect
from engine.instructions import parse_instruction
from engine.tabular import extract_from_dataframes
from sources.bps_utils import METRIC_SYNONYMS
from sources.registry import ADAPTERS, AdapterContext

FILE_ID = "1b-6CkunjY_LAkKo6C419Akqim1O5V3nm"
SHARE_URL = f"https://drive.google.com/file/d/{FILE_ID}/view"
DIRECT_URL = f"https://drive.google.com/uc?export=download&id={FILE_ID}"


# ---------------------------------------------------------------------------
# 1. Resolusi URL Google Drive
# ---------------------------------------------------------------------------

def test_resolve_gdrive_share_view_url():
    assert _resolve_gdrive_url(SHARE_URL) == DIRECT_URL


def test_resolve_gdrive_open_id_url():
    url = f"https://drive.google.com/open?id={FILE_ID}"
    assert _resolve_gdrive_url(url) == DIRECT_URL


def test_resolve_gdrive_direct_url_is_idempotent():
    assert _resolve_gdrive_url(DIRECT_URL) == DIRECT_URL


def test_resolve_leaves_non_gdrive_urls_alone():
    assert _resolve_gdrive_url("https://bps.go.id/data.pdf") == (
        "https://bps.go.id/data.pdf"
    )
    assert _resolve_gdrive_url("https://example.test/?id=abc") == (
        "https://example.test/?id=abc"
    )


def test_fetch_and_detect_uses_resolved_url(monkeypatch):
    captured = {}

    class _FakeResponse:
        url = "https://drive.usercontent.google.com/download"
        headers = {"Content-Type": "application/pdf"}
        content = b"%PDF-1.4 fake"

        def raise_for_status(self):
            pass

    def fake_get(url, **_kwargs):
        captured["url"] = url
        return _FakeResponse()

    import engine.detector as detector

    monkeypatch.setattr(detector.requests, "get", fake_get)

    payload = fetch_and_detect(SHARE_URL)

    assert captured["url"] == DIRECT_URL
    assert payload.kind == "pdf"


# ---------------------------------------------------------------------------
# 2. Frame sel region gabungan ditolak
# ---------------------------------------------------------------------------

def _merged_region_frame():
    return pd.DataFrame(
        {
            "NO": ["\n".join(str(i) for i in range(1, 14))],
            "KABUPATEN/KOTA": ["\n".join(KALSEL_WILAYAHS[1:])],
            "JUMLAH KASUS TUBERKULOSIS": ["9528"],
        }
    )


def test_merged_region_cell_is_rejected():
    instruction = parse_instruction(
        "Cakupan penemuan Kasus TBC kab/kota tahun 2024",
        default_wilayah="Prov. Kalimantan Selatan",
    )
    frame = _merged_region_frame()
    frame.attrs["metric_context"] = (
        "JUMLAH SEMUA KASUS TUBERKULOSIS YANG DITEMUKAN DAN DIOBATI "
        "TAHUN 2024"
    )

    result = extract_from_dataframes([frame], instruction, 2024, "pdf")

    assert result.status == "NEED_PARSER"


def test_normal_per_region_rows_still_extracted():
    instruction = parse_instruction(
        "Cakupan penemuan Kasus TBC kab/kota tahun 2024",
        default_wilayah="Prov. Kalimantan Selatan",
    )
    frame = pd.DataFrame(
        {
            "KABUPATEN/KOTA": KALSEL_WILAYAHS[1:],
            "JUMLAH KASUS TUBERKULOSIS": list(
                range(1, len(KALSEL_WILAYAHS))
            ),
        }
    )
    frame.attrs["metric_context"] = (
        "JUMLAH SEMUA KASUS TUBERKULOSIS YANG DITEMUKAN DAN DIOBATI "
        "TAHUN 2024"
    )

    result = extract_from_dataframes([frame], instruction, 2024, "pdf")

    assert result.status == "SUCCESS"
    assert len(result.value) == len(KALSEL_WILAYAHS) - 1


# ---------------------------------------------------------------------------
# 3. Sinonim tbc
# ---------------------------------------------------------------------------

def test_metric_synonyms_include_tbc():
    assert "tuberkulosis" in METRIC_SYNONYMS.get("tbc", [])
    assert "tbc" in METRIC_SYNONYMS.get("tuberkulosis", [])

    from extractors.pdf import _metric_context_matches

    assert _metric_context_matches(
        "Jumlah Tuberkulosis menurut wilayah",
        ["tbc"],
    )


# ---------------------------------------------------------------------------
# 4. Guard adapter tbc
# ---------------------------------------------------------------------------

def _tbc_spec():
    return next(spec for spec in ADAPTERS if spec.name == "tbc")


def test_tbc_rejects_pdf_only_kabkota_indicator():
    ctx = AdapterContext(
        url=SHARE_URL,
        tahun=2024,
        nama_indikator="Cakupan penemuan Kasus TBC",
        description=(
            "Kode 999 | Cakupan penemuan Kasus TBC | "
            "jumlah kasus per kab/kota tahun 2024"
        ),
    )

    assert _tbc_spec().matches(ctx) is False


def test_tbc_allows_indicator_when_opendata_present():
    ctx = AdapterContext(
        url="https://opendata.kalselprov.go.id/open-data/dataset/"
        "b43f5eef-565f-43f8-9f80-0aa9e0c079eb",
        tahun=2024,
        nama_indikator="Cakupan penemuan Kasus TBC",
        description=(
            "Kode 999 | Cakupan penemuan Kasus TBC | kab/kota"
        ),
    )

    assert _tbc_spec().matches(ctx) is True


def test_tbc_allows_pdf_only_for_province_indicator():
    ctx = AdapterContext(
        url=SHARE_URL,
        tahun=2024,
        nama_indikator="Cakupan penemuan Kasus TBC",
        description=(
            "Kode 999 | Cakupan penemuan Kasus TBC | "
            "prov. kalimantan selatan"
        ),
    )

    assert _tbc_spec().matches(ctx) is True


# ---------------------------------------------------------------------------
# 5. Live end-to-end (jaringan)
# ---------------------------------------------------------------------------

def test_share_url_end_to_end_live():
    try:
        probe = requests.get(
            DIRECT_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
            stream=True,
        )
        probe.raise_for_status()
        probe.close()
    except Exception as error:
        pytest.skip(f"Jaringan Google Drive tidak tersedia: {error}")

    from engine.router import scrape_source_auto

    result = scrape_source_auto(
        url=SHARE_URL,
        tahun=2024,
        description=(
            "Kode 999 | Cakupan penemuan Kasus TBC | "
            "jumlah kasus per kab/kota tahun 2024"
        ),
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Cakupan penemuan Kasus TBC",
    )

    assert result.status == "SUCCESS", (
        f"{result.status}: {result.message}"
    )
    assert "gender_chart_sum" in result.evidence
    assert len(result.value) == 13
    assert result.value["Kota Banjarmasin"] == 2767.0
    assert sum(result.value.values()) == 9528.0
    assert not any(
        str(e).startswith("value_column=no")
        for e in result.evidence
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
