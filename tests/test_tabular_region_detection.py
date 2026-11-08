import pandas as pd

from engine.instructions import Instruction
from engine.tabular import _find_region_columns, extract_from_dataframes


TARGETS = [
    "Prov. Kalimantan Selatan",
    "Kab. Tanah Laut",
    "Kab. Kotabaru",
    "Kab. Banjar",
    "Kab. Barito Kuala",
    "Kab. Tapin",
    "Kab. Hulu Sungai Selatan",
    "Kab. Hulu Sungai Tengah",
    "Kab. Hulu Sungai Utara",
    "Kab. Tabalong",
    "Kab. Tanah Bumbu",
    "Kab. Balangan",
    "Kota Banjarmasin",
    "Kota Banjarbaru",
]


def _instruction(targets=None):
    targets = targets or TARGETS
    return Instruction(
        raw="Pertumbuhan Ekonomi (%)",
        metric_terms=["pertumbuhan", "ekonomi"],
        target_wilayahs=targets,
        wants_multi_wilayah=len(targets) > 1,
    )


def test_row_oriented_table_does_not_treat_metric_header_as_region_column():
    frame = pd.DataFrame(
        {
            "Kabupaten/Kota + Provinsi": [
                "KALIMANTAN SELATAN", "TANAH LAUT", "KOTABARU",
            ],
            "Pertumbuhan Ekonomi Menurut Kabupaten/Kota di Provinsi Kalimantan Selatan (Persen) 2024": [
                5.06, 5.01, 5.34,
            ],
        }
    )
    instruction = _instruction(TARGETS[:3])

    region_columns = _find_region_columns(frame, instruction)
    result = extract_from_dataframes([frame], instruction, 2024, "test")

    assert region_columns == ["Kabupaten/Kota + Provinsi"]
    assert result.status == "SUCCESS"
    assert result.value == {
        "Prov. Kalimantan Selatan": 5.06,
        "Kab. Tanah Laut": 5.01,
        "Kab. Kotabaru": 5.34,
    }


def test_wide_table_still_recognizes_exact_region_headers():
    frame = pd.DataFrame(
        {
            "Tahun": [2023, 2024],
            "Tanah Laut": [4.8, 5.01],
            "Kotabaru": [5.1, 5.34],
        }
    )
    instruction = _instruction(["Kab. Tanah Laut", "Kab. Kotabaru"])

    result = extract_from_dataframes([frame], instruction, 2024, "test")

    assert result.status == "SUCCESS"
    assert result.value == {
        "Kab. Tanah Laut": 5.01,
        "Kab. Kotabaru": 5.34,
    }


def test_numeric_metric_header_containing_region_words_is_not_wide_region():
    frame = pd.DataFrame(
        {
            "Kabupaten/Kota + Provinsi": ["TANAH LAUT", "KOTABARU"],
            "Pertumbuhan Ekonomi Menurut Kabupaten/Kota di Provinsi Kalimantan Selatan (Persen) 2024": [
                5.01, 5.34,
            ],
        }
    )
    instruction = _instruction(["Kab. Tanah Laut", "Kab. Kotabaru"])

    region_columns = _find_region_columns(frame, instruction)

    assert len(region_columns) == 1
    assert region_columns[0] == "Kabupaten/Kota + Provinsi"
