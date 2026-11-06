import re
from types import SimpleNamespace

import pandas as pd

from engine.instructions import parse_instruction
from extractors import pdf
from config import AUTO_CONFIDENCE_THRESHOLD, KALSEL_WILAYAHS
from engine.tabular import extract_from_dataframes


def test_table_extraction_prioritizes_pages_matching_metric_year_region(
    monkeypatch,
):
    pages = []
    table_calls = []
    for index in range(50):
        relevant = index in {20, 21}
        text = (
            "Pertumbuhan Ekonomi Kalimantan Selatan 2024"
            if relevant
            else f"Informasi umum halaman {index}"
        )

        def extract_tables(*_args, page_index=index, **_kwargs):
            table_calls.append(page_index)
            return []

        pages.append(
            SimpleNamespace(
                extract_text=lambda text=text, **_kwargs: text,
                extract_tables=extract_tables,
            )
        )

    class FakePdf:
        def __init__(self):
            self.pages = pages

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(pdf.pdfplumber, "open", lambda *_args: FakePdf())
    instruction = parse_instruction(
        "Pertumbuhan Ekonomi Prov. Kalimantan Selatan 2024",
        default_wilayah="Prov. Kalimantan Selatan",
    )

    frames, texts = pdf._tables_from_pdf(b"pdf", instruction, 2024)

    assert frames == []
    assert len(texts) == 50
    assert table_calls
    assert 20 in table_calls
    assert 21 in table_calls
    assert 0 not in table_calls
    assert len(table_calls) <= 30


def test_ocr_is_skipped_when_all_pages_have_text_layer(monkeypatch):
    monkeypatch.setattr(pdf, "OCR_ENABLED", True)
    monkeypatch.setattr(
        pdf.pdfplumber,
        "open",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("digital PDF should not be reopened for OCR")
        ),
    )

    assert pdf._ocr_fallback(
        b"pdf",
        parse_instruction("Pertumbuhan Ekonomi"),
        2024,
        ["Teks halaman 1", "Teks halaman 2"],
    ) is None


def test_multi_region_request_keeps_region_key_for_single_match():
    instruction = parse_instruction(
        "Pertumbuhan Ekonomi untuk seluruh kabupaten/kota dan provinsi",
        default_wilayah="Prov. Kalimantan Selatan",
    )
    frame = pd.DataFrame(
        {
            "Wilayah": ["Kab. Tanah Laut"],
            "Pertumbuhan Ekonomi 2024": [5.1],
        }
    )

    result = extract_from_dataframes(
        [frame],
        instruction,
        2024,
        source_type="pdf",
    )

    assert result.value == {"Kab. Tanah Laut": 5.1}


def test_multi_region_request_does_not_assign_unknown_single_value():
    instruction = parse_instruction(
        "Pertumbuhan Ekonomi untuk seluruh kabupaten/kota dan provinsi",
        default_wilayah="Prov. Kalimantan Selatan",
    )
    frame = pd.DataFrame(
        {
            "Pertumbuhan Ekonomi 2024": [5.1],
        }
    )

    result = extract_from_dataframes(
        [frame],
        instruction,
        2024,
        source_type="pdf",
    )

    assert result.status == "NEED_PARSER"


def test_instruction_ignores_indicator_metadata_as_metric_terms():
    instruction = parse_instruction(
        "Kode 40 | Indeks Ketimpangan Gender (IKG), "
        "cakupan IDSD seluruh kabupaten/kota dan provinsi"
    )

    assert instruction.metric_terms == [
        "indeks",
        "ketimpangan",
        "gender",
        "ikg",
    ]


def test_pdf_table_uses_page_title_and_exact_year_column_for_confidence():
    instruction = parse_instruction(
        "Indeks Ketimpangan Gender (IKG) untuk seluruh kabupaten/kota "
        "dan provinsi",
        default_wilayah="Prov. Kalimantan Selatan",
    )
    frame = pd.DataFrame(
        {
            "Wilayah": KALSEL_WILAYAHS,
            "2020": [0.50] * len(KALSEL_WILAYAHS),
            "2021": [0.49] * len(KALSEL_WILAYAHS),
        }
    )
    frame.attrs["metric_context"] = (
        "Tabel 2. Perkembangan Indeks Ketimpangan Gender (IKG) "
        "Kabupaten/Kota di Kalimantan Selatan"
    )

    result = extract_from_dataframes(
        [frame],
        instruction,
        2021,
        source_type="pdf",
    )

    assert result.status == "SUCCESS"
    assert len(result.value) == len(KALSEL_WILAYAHS)
    assert "2021" in result.message
    assert result.confidence >= AUTO_CONFIDENCE_THRESHOLD


def test_text_table_fallback_marks_complete_metric_table_as_success():
    instruction = parse_instruction(
        "Indeks Ketimpangan Gender (IKG) untuk seluruh kabupaten/kota "
        "dan provinsi",
        default_wilayah="Prov. Kalimantan Selatan",
    )
    labels = [
        re.sub(r"^(?:Prov\.|Kab\.|Kota)\s+", "", region)
        for region in KALSEL_WILAYAHS
    ]
    page_text = "\n".join(
        [
            "Tabel 2. Perkembangan Indeks Ketimpangan Gender (IKG)",
            "Kabupaten/Kota 2020 2021",
            *[f"{label} 0,500 0,490" for label in labels],
        ]
    )

    result = pdf._text_table_fallback(
        b"pdf",
        instruction,
        2021,
        [page_text],
    )

    assert result is not None
    assert result.status == "SUCCESS"
    assert len(result.value) == len(KALSEL_WILAYAHS)
    assert result.confidence >= AUTO_CONFIDENCE_THRESHOLD


def test_text_table_fallback_ignores_other_metrics_with_same_region_years():
    instruction = parse_instruction(
        "Indeks Ketimpangan Gender (IKG) untuk seluruh kabupaten/kota "
        "dan provinsi",
        default_wilayah="Prov. Kalimantan Selatan",
    )
    page_text = "\n".join(
        [
            "Tabel Perkembangan Jumlah Penduduk",
            "Kabupaten/Kota 2020 2021",
            "Tanah Laut 1000 1100",
        ]
    )

    result = pdf._text_table_fallback(
        b"pdf",
        instruction,
        2021,
        [page_text],
    )

    assert result is None


def test_text_table_fallback_requires_multiple_indicator_terms():
    instruction = parse_instruction(
        "Indeks Ketimpangan Gender (IKG) untuk seluruh kabupaten/kota "
        "dan provinsi",
        default_wilayah="Prov. Kalimantan Selatan",
    )
    page_text = "\n".join(
        [
            "Tabel Perkembangan Indeks Harga",
            "Kabupaten/Kota 2020 2021",
            "Tanah Laut 1000 1100",
        ]
    )

    result = pdf._text_table_fallback(
        b"pdf",
        instruction,
        2021,
        [page_text],
    )

    assert result is None
