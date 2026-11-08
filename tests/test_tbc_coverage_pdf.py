
import re


def test_tbc_coverage_pattern():
    pattern = re.compile(
        r"CAKUPAN\s+PENEMUAN\s+KASUS\s+"
        r"TUBERKULOSIS\s*\(\s*%\s*\)\s*"
        r"([0-9]+(?:[.,][0-9]+)?)",
        re.IGNORECASE,
    )

    text = """
    TABEL 56
    PROVINSI KALIMANTAN SELATAN
    TAHUN 2024

    CAKUPAN PENEMUAN KASUS TUBERKULOSIS (% ) 84,0

    CAKUPAN PENEMUAN KASUS TUBERKULOSIS ANAK (% ) 132,9
    """

    normalized = re.sub(r"\s+", " ", text)

    match = pattern.search(normalized)

    assert match is not None
    assert match.group(1) == "84,0"
