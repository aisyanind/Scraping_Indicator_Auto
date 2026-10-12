"""
Shared region utilities — fungsi normalisasi wilayah yang digunakan
semua adapter. Satu sumber kebenaran untuk semua kode wilayah.
"""

import re

# =====================================================================
# KALSEL Kab/Kota — satu sumber kebenaran
# =====================================================================

KALSEL_KAB_KOTA = [
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

# =====================================================================
# REGION_ALIASES — mapping nama wilayah → bentuk kanonik
# =====================================================================

REGION_ALIASES = {
    # Kalimantan Selatan
    "banjar baru": "banjarbaru",
    "banjarbaru": "banjarbaru",
    "tanah laut": "tanah laut",
    "kotabaru": "kotabaru",
    "barito kuala": "barito kuala",
    "hulu sungai selatan": "hulu sungai selatan",
    "hulu sungai tengah": "hulu sungai tengah",
    "hulu sungai utara": "hulu sungai utara",
    "tabalong": "tabalong",
    "tanah bumbu": "tanah bumbu",
    "balangan": "balangan",
    "banjarmasin": "banjarmasin",
    "kalimantan selatan": "kalimantan selatan",
    "kalsel": "kalimantan selatan",
    # Provinsi lain
    "kalimantan tengah": "kalimantan tengah",
    "kalteng": "kalimantan tengah",
    "kalimantan timur": "kalimantan timur",
    "kaltim": "kalimantan timur",
    "kalimantan utara": "kalimantan utara",
    "kalimantan barat": "kalimantan barat",
    "kalbar": "kalimantan barat",
    "jawa barat": "jawa barat",
    "jabar": "jawa barat",
    "jawa tengah": "jawa tengah",
    "jateng": "jawa tengah",
    "jawa timur": "jawa timur",
    "jatim": "jawa timur",
    "dki jakarta": "dki jakarta",
    "jakarta": "dki jakarta",
    "banten": "banten",
    "bali": "bali",
    "nusa tenggara barat": "nusa tenggara barat",
    "ntb": "nusa tenggara barat",
    "nusa tenggara timur": "nusa tenggara timur",
    "ntt": "nusa tenggara timur",
    "sumatera utara": "sumatera utara",
    "sumut": "sumatera utara",
    "sumatera barat": "sumatera barat",
    "sumbar": "sumatera barat",
    "sumatera selatan": "sumatera selatan",
    "sumsel": "sumatera selatan",
    "sulawesi selatan": "sulawesi selatan",
    "sulsel": "sulawesi selatan",
    "sulawesi tengah": "sulawesi tengah",
    "sulteng": "sulawesi tengah",
    "sulawesi utara": "sulawesi utara",
    "sulut": "sulawesi utara",
    "papua": "papua",
    "papua barat": "papua barat",
    "gorontalo": "gorontalo",
    "maluku": "maluku",
    "maluku utara": "maluku utara",
    "kepulauan riau": "kepulauan riau",
    "kepri": "kepulauan riau",
    "bangka belitung": "bangka belitung",
    "babel": "bangka belitung",
    "lampung": "lampung",
    "aceh": "aceh",
    "riau": "riau",
    "jambi": "jambi",
    "bengkulu": "bengkulu",
}

# =====================================================================
# Normalisasi teks
# =====================================================================


def normalize_text(value):
    """Lowercase + collapse whitespace."""
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def normalize_region(value):
    """
    Normalisasi nama wilayah ke bentuk kanonik.

    Contoh:
        "Provinsi Kalimantan Selatan" -> "prov. kalimantan selatan"
        "Kabupaten Tanah Laut" -> "kab. tanah laut"
        "Kalsel" -> "kalimantan selatan"
        "Banjar Baru" -> "banjarbaru"
    """
    text = normalize_text(value)

    # Normalisasi prefix
    for old, new in (
        ("provinsi ", "prov. "),
        ("prov ", "prov. "),
        ("kabupaten ", "kab. "),
        ("kab ", "kab. "),
    ):
        if text.startswith(old):
            text = new + text[len(old):]
            break

    # Special case: "kalimantan selatan" -> "prov. kalimantan selatan"
    if text == "kalimantan selatan":
        text = "prov. kalimantan selatan"

    # Alias lookup
    for alias, canonical in REGION_ALIASES.items():
        if alias in text:
            text = text.replace(alias, canonical)

    return text


def resolve_targets(description, default_wilayah=None, available_regions=None):
    """
    Resolve target wilayah dari description/instruksi.

    Return list of (wilayah, is_kab_kota) tuples.
    """
    lower = str(description or "").lower()

    is_provinsi = any(w in lower for w in (
        "provinsi", "prov.", "prov ", "seluruh prov",
        "se-kalsel", "se kalsel", "kalsel",
    ))

    is_kab_kota = any(w in lower for w in (
        "kab/kota", "kabupaten/kota", "kabupaten kota",
        "seluruh kab", "tiap kab", "masing-masing kab",
    ))

    if is_provinsi and not is_kab_kota:
        return [("Prov. Kalimantan Selatan", False)]

    if is_kab_kota:
        regions = available_regions or KALSEL_KAB_KOTA
        return [(r, True) for r in regions]

    # Default: kab/kota semua
    regions = available_regions or KALSEL_KAB_KOTA
    return [(r, True) for r in regions]
