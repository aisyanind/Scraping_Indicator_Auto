from engine.instructions import parse_instruction
from engine.formula import calculate_formula


def test_instruction_multi():
    ins = parse_instruction(
        "IDSD Kab/Kota di Kalsel dan Prov. Kalsel"
    )
    assert ins.wants_multi_wilayah
    assert len(ins.target_wilayahs) == 14


def test_formula():
    assert calculate_formula(
        "(S1/S2)*100",
        {"S1": 50, "S2": 200, "S3": None},
    ) == 25.0
