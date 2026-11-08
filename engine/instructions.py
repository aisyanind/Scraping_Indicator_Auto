import re
from dataclasses import dataclass

from config import DEFAULT_WILAYAH, KALSEL_WILAYAHS


STOPWORDS = {
    "ambil", "data", "nilai", "tahun", "di", "dan", "dari", "untuk",
    "yang", "seluruh", "semua", "kalsel", "kalimantan", "selatan",
    "prov", "provinsi", "kab", "kabupaten", "kota",
    "kode", "cakupan", "idsd", "sumber", "indikator",
}


@dataclass
class Instruction:
    raw: str
    metric_terms: list[str]
    target_wilayahs: list[str]
    wants_multi_wilayah: bool
    coverage_scope: str = ""


def normalize_coverage_scope(value: str | None) -> str:
    """Normalize the form's explicit coverage selection to a stable code."""
    raw = str(value or "").strip().upper()
    if not raw:
        return ""

    compact = re.sub(r"[^A-Z0-9]+", "_", raw).strip("_")

    aliases = {
        # Province only.
        "PROV": "PROV",
        "PROVINSI": "PROV",
        "PROVINSI_KALIMANTAN_SELATAN": "PROV",
        "PROV_KALSEL": "PROV",
        # Province + all 13 regencies/cities.
        "ALL": "ALL",
        "PROV_KABKOTA": "ALL",
        "PROV_KAB_KOTA": "ALL",
        "PROVINSI_KABKOTA": "ALL",
        "PROVINSI_KAB_KOTA": "ALL",
        "PROVINSI_KABUPATEN_KOTA": "ALL",
        "PROVINSI_PLUS_KAB_KOTA": "ALL",
        "PROVINSI_PLUS_KABUPATEN_KOTA": "ALL",
        "PROVINSI_DAN_KAB_KOTA": "ALL",
        "PROVINSI_DAN_KABUPATEN_KOTA": "ALL",
        "PROVINSI_SELURUH_KAB_KOTA": "ALL",
        "PROVINSI_SELURUH_KABUPATEN_KOTA": "ALL",
        "PROVINSI_PLUS_SELURUH_KAB_KOTA": "ALL",
        "PROVINSI_PLUS_SELURUH_KABUPATEN_KOTA": "ALL",
        "PROVINSI_SELURUH_KAB_KOTA_KALIMANTAN_SELATAN": "ALL",
        # All 13 regencies/cities, without the province row.
        "KABKOTA": "KABKOTA",
        "KAB_KOTA": "KABKOTA",
        "KABUPATEN_KOTA": "KABKOTA",
        "SELURUH_KAB_KOTA": "KABKOTA",
        "SELURUH_KABUPATEN_KOTA": "KABKOTA",
        # One selected region.
        "SPECIFIC": "SPECIFIC",
        "SINGLE": "SPECIFIC",
        "SATU_WILAYAH": "SPECIFIC",
        "SATU_WILAYAH_TERTENTU": "SPECIFIC",
    }
    return aliases.get(compact, "")


def parse_instruction(
    text: str,
    default_wilayah: str = "",
    cakupan_wilayah: str | None = None,
) -> Instruction:
    raw = str(text or "").strip()
    lower = raw.lower()
    explicit_scope = normalize_coverage_scope(cakupan_wilayah)

    # Prefer the structured form value. Text inference remains as a
    # compatibility fallback for jobs created before Cakupan_Wilayah existed.
    if explicit_scope == "PROV":
        targets = [KALSEL_WILAYAHS[0]]
    elif explicit_scope == "ALL":
        targets = KALSEL_WILAYAHS[:]
    elif explicit_scope == "KABKOTA":
        targets = KALSEL_WILAYAHS[1:]
    elif explicit_scope == "SPECIFIC":
        targets = [default_wilayah or DEFAULT_WILAYAH]
    else:
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

        # Important: the literal dropdown label "Provinsi + Kab/Kota"
        # contains both terms but not the old phrase "dan Prov.".
        if wants_kab_kota and re.search(r"\bprov(?:insi)?\b", lower):
            wants_prov = True

        if wants_kab_kota:
            if wants_prov:
                targets = KALSEL_WILAYAHS[:]
            else:
                targets = KALSEL_WILAYAHS[1:]
            explicit_scope = "ALL" if wants_prov else "KABKOTA"
        else:
            targets = [default_wilayah or DEFAULT_WILAYAH]
            explicit_scope = "PROV" if targets == [KALSEL_WILAYAHS[0]] else "SPECIFIC"

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
        coverage_scope=explicit_scope,
    )
