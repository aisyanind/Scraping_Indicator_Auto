"""
Regresi fallback grafik gender (Gambar 6.1 Profil Kesehatan Kalsel 2024)
dan guard terkait:

1. Grafik batang P+L per wilayah -> nilai = jumlah dua angka;
   angka tahun dan legenda huruf tunggal tidak boleh ikut.
2. Guard metric di extract_from_dataframes menolak frame tabel
   yang tidak membahas indikator (mis. tabel rumah sakit).
3. Adapter registry `tbc` menolak indikator "jumlah kasus".
4. _line_fallback tidak memakai angka tahun sebagai nilai.
"""

import pytest
from types import SimpleNamespace

from config import AUTO_CONFIDENCE_THRESHOLD
from engine.instructions import parse_instruction
from engine.tabular import _frame_metric_relevant
from extractors import pdf
from sources.registry import ADAPTERS, AdapterContext


def _w(text, top, x0=100.0):
    return {"text": text, "top": float(top), "x0": float(x0)}


# Fixture sebaran kata halaman 102 (Gambar 6.1), 13 kab/kota.
# Band label per wilayah: (prev_label_end, label_end]; band pertama
# [label_end-20, label_end]. Setiap band tepat 2 angka (P dan L).
_CHART_WORDS = [
    _w("Jumlah", 96, 60),
    _w("Kasus", 96, 120),
    _w("Tuberkulosis", 96, 160),
    _w("Tahun", 96, 300),
    _w("2024", 96, 350),   # angka tahun: wajib difilter
    _w("P", 96, 450),      # legenda grafik: dibuang dari stream
    _w("L", 96, 470),      # legenda grafik: dibuang dari stream
    # Banjarbaru -> band [88, 108]
    _w("288", 100, 250),
    _w("Kota", 108, 40),
    _w("Banjarbaru", 108, 80),
    _w("438", 108, 260),
    # Banjarmasin -> band (108, 128]
    _w("1.041", 116, 331),
    _w("1.726", 124, 482),
    _w("Kota", 128, 40),
    _w("Banjarmasin", 128, 80),
    # Balangan -> band (128, 144]
    _w("165", 136, 189),
    _w("195", 140, 221),
    _w("Kab.", 144, 40),
    _w("Balangan", 144, 80),
    # Tanah Bumbu -> band (144, 160]
    _w("194", 152, 189),
    _w("320", 156, 221),
    _w("Kab.", 160, 40),
    _w("Tanah", 160, 80),
    _w("Bumbu", 160, 120),
    # Tabalong -> band (160, 176]
    _w("182", 168, 189),
    _w("324", 172, 221),
    _w("Kab.", 176, 40),
    _w("Tabalong", 176, 80),
    # Hulu Sungai Utara -> band (176, 192]
    _w("232", 184, 189),
    _w("293", 188, 221),
    _w("Kab.", 184, 40),
    _w("Hulu", 184, 80),
    _w("Sungai", 184, 120),
    _w("Utara", 192, 80),
    # Hulu Sungai Tengah -> band (192, 208]
    _w("257", 200, 189),
    _w("404", 204, 221),
    _w("Kab.", 200, 40),
    _w("Hulu", 200, 80),
    _w("Sungai", 200, 120),
    _w("Tengah", 208, 80),
    # Hulu Sungai Selatan -> band (208, 224]
    _w("150", 216, 189),
    _w("238", 224, 221),
    _w("Kab.", 216, 40),
    _w("Hulu", 216, 80),
    _w("Sungai", 216, 120),
    _w("Selatan", 224, 80),
    # Tapin -> band (224, 240]
    _w("94", 232, 189),
    _w("163", 240, 221),
    _w("Kab.", 240, 40),
    _w("Tapin", 240, 80),
    # Barito Kuala -> band (240, 260]
    _w("147", 248, 189),
    _w("213", 256, 221),
    _w("Kab.", 260, 40),
    _w("Barito", 260, 80),
    _w("Kuala", 260, 130),
    # Banjar -> band (260, 276]
    _w("381", 268, 189),
    _w("665", 272, 221),
    _w("Kab.", 276, 40),
    _w("Banjar", 276, 80),
    # Kotabaru -> band (276, 292]
    _w("325", 284, 189),
    _w("424", 288, 221),
    _w("Kab.", 292, 40),
    _w("Kotabaru", 292, 80),
    # Tanah Laut -> band (292, 308]
    _w("271", 300, 189),
    _w("398", 304, 221),
    _w("Kab.", 308, 40),
    _w("Tanah", 308, 80),
    _w("Laut", 308, 120),
    # Narasi di bawah grafik: di luar semua band
    _w("Menurut", 450, 40),
    _w("Kota", 450, 100),
    _w("Banjarmasin", 450, 140),
    _w("2.767", 450, 200),
    _w("kasus", 450, 260),
    _w("9.528", 460, 40),
]

_PAGE_TEXT = (
    "Jumlah kasus Tuberkulosis menurut Jenis Kelamin "
    "per Kabupaten/Kota di Kalimantan Selatan Tahun 2024\n"
    "Laki-laki Perempuan\n"
    "Kab. Tanah Laut Kotabaru Banjarmasin\n"
)


def _instruction():
    return parse_instruction(
        "Jumlah kasus Tuberkulosis (kasus) kab/kota tahun 2024",
    )


class _FakePdf:
    def __init__(self, words, text=_PAGE_TEXT):
        self.pages = [
            SimpleNamespace(
                extract_words=lambda: [dict(w) for w in words],
                extract_text=lambda **_kwargs: text,
            )
        ]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


# ---------------------------------------------------------------------------
# 1. Fallback grafik gender
# ---------------------------------------------------------------------------

def test_gender_chart_sums_two_numbers_per_region():
    values = pdf._gender_chart_from_words(_CHART_WORDS, _instruction(), 2024)

    assert values is not None
    assert len(values) == 13
    assert values["Kota Banjarmasin"] == 2767.0
    assert values["Kota Banjarbaru"] == 726.0  # 2024 tidak ikut terjumlah
    assert values["Kab. Banjar"] == 1046.0
    assert sum(values.values()) == 9528.0


def test_gender_chart_rejects_band_with_wrong_number_count():
    words = [
        _w("Kab.", 144, 40),
        _w("Balangan", 144, 80),
        _w("165", 136, 189),
        _w("195", 140, 221),
        _w("999", 142, 250),  # angka ketiga di band yang sama
    ]

    values = pdf._gender_chart_from_words(words, _instruction(), 2024)

    assert values is None


def test_gender_chart_skips_letter_spaced_labels():
    # Tabel berformat "K O T A  B A N J A R M A S I N" (mis. Tabel 57)
    # terpecah jadi huruf tunggal dan harus dibuang dari stream.
    words = [
        _w("K", 128, 40),
        _w("O", 128, 46),
        _w("T", 128, 52),
        _w("A", 128, 58),
        _w("B", 128, 70),
        _w("A", 128, 76),
        _w("N", 128, 82),
        _w("J", 128, 88),
        _w("A", 128, 94),
        _w("R", 128, 100),
        _w("M", 128, 106),
        _w("A", 128, 112),
        _w("S", 128, 118),
        _w("I", 128, 124),
        _w("N", 128, 130),
        _w("1.726", 124, 300),
        _w("1.041", 116, 320),
    ]

    values = pdf._gender_chart_from_words(words, _instruction(), 2024)

    assert values is None


def test_gender_chart_fallback_requires_gender_context(monkeypatch):
    text = "Jumlah kasus Tuberkulosis tahun 2024\nTanah Laut 669\n"
    monkeypatch.setattr(
        pdf.pdfplumber,
        "open",
        lambda *_args: _FakePdf(_CHART_WORDS, text=text),
    )

    result = pdf._gender_chart_fallback(
        b"pdf",
        _instruction(),
        2024,
        [text],
    )

    assert result is None


def test_gender_chart_fallback_requires_metric_context(monkeypatch):
    text = "Jenis Kelamin Laki-laki Perempuan Tahun 2024\n"
    monkeypatch.setattr(
        pdf.pdfplumber,
        "open",
        lambda *_args: _FakePdf(_CHART_WORDS, text=text),
    )

    result = pdf._gender_chart_fallback(
        b"pdf",
        _instruction(),
        2024,
        [text],
    )

    assert result is None


def test_gender_chart_fallback_success_on_full_chart(monkeypatch):
    monkeypatch.setattr(
        pdf.pdfplumber,
        "open",
        lambda *_args: _FakePdf(_CHART_WORDS),
    )

    result = pdf._gender_chart_fallback(
        b"pdf",
        _instruction(),
        2024,
        [_PAGE_TEXT],
    )

    assert result is not None
    assert result.status == "SUCCESS"
    assert result.confidence >= AUTO_CONFIDENCE_THRESHOLD
    assert result.value["Kota Banjarmasin"] == 2767.0
    assert len(result.value) == 13
    assert "gender_chart_sum" in result.evidence
    assert "page=1" in result.evidence
    assert "regions=13/13" in result.evidence


def test_gender_chart_fallback_partial_chart_needs_review(monkeypatch):
    # 8 dari 13 wilayah (potong sebelum Banjar) -> coverage 0.61 -> REVIEW.
    words = _CHART_WORDS[:49]
    monkeypatch.setattr(
        pdf.pdfplumber,
        "open",
        lambda *_args: _FakePdf(words),
    )

    result = pdf._gender_chart_fallback(
        b"pdf",
        _instruction(),
        2024,
        [_PAGE_TEXT],
    )

    assert result is not None
    assert result.status == "REVIEW"
    assert result.confidence < AUTO_CONFIDENCE_THRESHOLD
    assert len(result.value) == 8


# ---------------------------------------------------------------------------
# 2. Guard metric frame tabel
# ---------------------------------------------------------------------------

def test_frame_metric_rejects_hospital_table_for_tbc_indicator():
    context = (
        "Daftar Rumah Sakit di Kalimantan Selatan Tahun 2024 "
        "Jumlah Tempat Tidur"
    )

    assert not _frame_metric_relevant(
        context,
        ["NAMA RUMAH SAKIT", "JUMLAH TEMPAT TIDUR"],
        ["jumlah", "kasus", "tuberkulosis", "2024"],
    )


def test_frame_metric_accepts_context_with_specific_terms():
    context = "Jumlah kasus Tuberkulosis per Kabupaten/Kota Tahun 2024"

    assert _frame_metric_relevant(
        context,
        ["Kabupaten/Kota", "2024"],
        ["jumlah", "kasus", "tuberkulosis", "2024"],
    )


def test_frame_metric_without_context_is_not_restricted():
    # CSV/Excel tanpa context halaman tidak boleh terblokir.
    assert _frame_metric_relevant(
        "",
        ["wilayah", "2024"],
        ["jumlah", "kasus", "tuberkulosis", "2024"],
    )


# ---------------------------------------------------------------------------
# 3. Adapter registry tbc
# ---------------------------------------------------------------------------

def test_tbc_adapter_rejects_jumlah_kasus_indicator():
    tbc = next(spec for spec in ADAPTERS if spec.name == "tbc")

    jumlah_ctx = AdapterContext(
        url="https://example.test/data",
        tahun=2024,
        nama_indikator="Jumlah kasus Tuberkulosis (kasus) kab/kota",
    )
    assert not tbc.matches(jumlah_ctx)

    cakupan_ctx = AdapterContext(
        url="https://opendata.kalselprov.go.id/open-data/dataset/"
        "b43f5eef-565f-43f8-9f80-0aa9e0c079eb",
        tahun=2024,
        nama_indikator="Cakupan penemuan kasus Tuberkulosis kab/kota",
    )
    assert tbc.matches(cakupan_ctx)


# ---------------------------------------------------------------------------
# 4. _line_fallback tidak memakai angka tahun
# ---------------------------------------------------------------------------

def test_line_fallback_skips_year_value():
    instruction = parse_instruction(
        "Jumlah kasus Tuberkulosis kab/kota tahun 2024",
        default_wilayah="Kab. Banjar",
    )

    result = pdf._line_fallback(
        b"pdf",
        instruction,
        2024,
        ["Kab. Banjar 1046 TAHUN 2024"],
    )

    assert result.status == "REVIEW"
    assert result.value == {"Kab. Banjar": 1046.0}
    assert "pdf_line_fallback" in result.evidence


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
