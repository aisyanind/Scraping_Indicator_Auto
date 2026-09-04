import re
from dataclasses import dataclass

from config import DEFAULT_WILAYAH, KALSEL_WILAYAHS


STOPWORDS = {
    "ambil", "data", "nilai", "tahun", "di", "dan", "dari", "untuk",
    "yang", "seluruh", "semua", "kalsel", "kalimantan", "selatan",
    "prov", "provinsi", "kab", "kabupaten", "kota",
}


@dataclass
class Instruction:
    raw: str
    metric_terms: list[str]
    target_wilayahs: list[str]
    wants_multi_wilayah: bool


def parse_instruction(text: str, default_wilayah: str = "") -> Instruction:
    raw = str(text or "").strip()
    lower = raw.lower()

    wants_kab_kota = any(
        marker in lower
        for marker in (
            "kab/kota",
            "kabupaten/kota",
            "kabupaten dan kota",
            "kab kota",
            "seluruh kab",
            "semua kab",
        )
    )
    wants_prov = any(
        marker in lower
        for marker in (
            "prov. kalsel",
            "prov kalsel",
            "provinsi kalsel",
            "provinsi kalimantan selatan",
            "prov. kalimantan selatan",
            "dan prov",
        )
    )

    if wants_kab_kota:
        if wants_prov:
            targets = KALSEL_WILAYAHS[:]
        else:
            targets = KALSEL_WILAYAHS[1:]
    else:
        targets = [default_wilayah or DEFAULT_WILAYAH]

    tokens = re.findall(r"[a-zA-Z0-9]+", lower)
    terms = []
    for token in tokens:
        if token in STOPWORDS:
            continue
        if len(token) <= 2:
            continue
        if token not in terms:
            terms.append(token)

    return Instruction(
        raw=raw,
        metric_terms=terms,
        target_wilayahs=targets,
        wants_multi_wilayah=len(targets) > 1,
    )
