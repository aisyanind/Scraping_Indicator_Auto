import re

from sources.bps_utils import (
    download_html,
    extract_available_years,
    extract_table_year,
    extract_value_near,
    parse_number_id,
    with_year_param,
)

# PDRB Kalsel ADHB dalam miliar rupiah: ~180 ribu (2019) s.d. ~300 ribu (2025).
PDRB_MIN = 50_000
PDRB_MAX = 1_000_000

_PDRB_CACHE = {}


def _validate_pdrb(body):
    """Validasi tambahan untuk halaman PDRB."""
    if "produk domestik" not in body.lower():
        raise RuntimeError(
            "Halaman tidak memuat konten PDRB yang diharapkan."
        )


def _extract_pdrb_total(html, tahun):
    """
    Ambil baris total PDRB dari JSON yang tertanam di halaman.

    Return (nilai, label) atau (None, alasan).
    """
    lowered = html.lower()
    candidates = []

    for match in re.finditer(r"produk domestik", lowered):
        tail = lowered[match.start():match.start() + 80]

        if "bruto" not in tail:
            continue

        if re.search(
            r"per\s*kapita|distribusi|pertumbuhan|konstan",
            lowered[match.start():match.start() + 120],
        ):
            continue

        segment = html[match.start():match.start() + 900]
        raw = extract_value_near(segment)

        if not raw:
            continue

        nilai = parse_number_id(raw)

        if nilai is None:
            continue

        candidates.append((nilai, raw))

    for nilai, raw in candidates:
        if PDRB_MIN <= nilai <= PDRB_MAX:
            return nilai, raw

    return None, (
        f"kandidat tidak masuk rentang wajar {PDRB_MIN}-{PDRB_MAX} "
        f"miliar Rp: {candidates}" if candidates else "label total tidak ditemukan"
    )


def scrape_bps_pdrb_page(url, tahun, description=None):
    """
    Return nilai PDRB ADHB (miliar rupiah) untuk satu tahun.

    URL Sumber boleh memuat ?year= apa pun -- parameter tahun pada
    URL diganti sesuai tahun yang diminta.
    """
    tahun = int(tahun)

    target_url = with_year_param(url, tahun)

    cache_key = (target_url, tahun)

    if cache_key in _PDRB_CACHE:
        return _PDRB_CACHE[cache_key]

    print(
        f"    [BPS-PDRB] Mengunduh tabel PDRB tahun {tahun}: "
        f"{target_url[:110]}"
    )

    html = download_html(target_url, tag="BPS-PDRB", validate=_validate_pdrb)

    table_year = extract_table_year(html)

    if table_year is not None and table_year != tahun:
        available = extract_available_years(html)

        raise RuntimeError(
            f"Tabel BPS yang diterisa untuk tahun {table_year}, "
            f"bukan {tahun}. Tahun tersedia: {available or 'tidak diketahui'}."
        )

    nilai, info = _extract_pdrb_total(html, tahun)

    if nilai is None:
        raise RuntimeError(
            f"Nilai total PDRB tahun {tahun} tidak ditemukan di halaman "
            f"BPS ({info})."
        )

    print(
        f"    [BPS-PDRB] PDRB {tahun} = {info} miliar Rp "
        f"(tahun_data={table_year})"
    )

    _PDRB_CACHE[cache_key] = float(nilai)

    return float(nilai)


__all__ = [
    "scrape_bps_pdrb_page",
]
