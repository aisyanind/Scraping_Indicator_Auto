import sys
sys.path.insert(0, "D:\\MagangHub\\Scraping_Indikator_Auto")

from engine.run_indicator import _sources_in_formula
from engine.formula import calculate_formula
from sources.legacy_bridge import _ok
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


if __name__ == "__main__":
    test_rumus_empty_defaults_to_s1()
    test_rumus_constant_no_source()
    test_rumus_references_sources()
    test_ok_empty_dict_returns_data_tidak_ada()
    test_ok_empty_list_returns_data_tidak_ada()
    test_ok_none_returns_data_tidak_ada()
    test_ok_valid_value_returns_success()
    test_tst_map_kalsel_filter_year()
    print("ALL TESTS PASSED")