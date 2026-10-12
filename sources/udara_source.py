import re
from io import BytesIO

import pdfplumber

from sources.bps_utils import (
    download_html,
    download_pdf,
    extract_available_years,
    extract_table_year,
    extract_value_near,
    find_pdf_url,
    is_press_release,
    parse_number_id,
    with_year_param,
    PAGE_REQUEST_HEADERS,
)

PENUMPANG_MIN = 1_000
PENUMPANG_MAX = 100_000_000

_CACHE = {}


def _extract_from_pdf(pdf_bytes, tahun):
    """
    Parse tabel dari PDF BPS, cari data jumlah penumpang angkutan udara.

    Return (nilai, info) atau (None, alasan).
    """
    tahun = int(tahun)
    candidates = []

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                continue

            text_lower = text.lower()

            is_air = (
                "angkutan udara" in text_lower
                or "penerbangan" in text_lower
                or "bandara" in text_lower
            )

            if not is_air:
                continue

            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []

            for table in tables:
                if not table or len(table) < 3:
                    continue

                header_text = " ".join(
                    str(cell or "").lower()
                    for cell in table[0]
                )

                if "penumpang" not in header_text:
                    continue

                total_row = None
                for row in table:
                    if not row:
                        continue
                    first_cell = str(row[0] or "").strip().lower()
                    if first_cell in ("total", "jumlah"):
                        total_row = row
                        break

                if total_row is None:
                    continue

                for cell in total_row[1:]:
                    raw = str(cell or "").strip()

                    if not raw:
                        continue

                    nilai = parse_number_id(raw)

                    if nilai is None:
                        continue

                    if 1900 <= nilai <= 2099:
                        continue

                    if PENUMPANG_MIN <= nilai <= PENUMPANG_MAX:
                        candidates.append(
                            (nilai, raw, page_num)
                        )

    if not candidates:
        return None, "tabel penumpang Total tidak ditemukan di PDF"

    best = max(candidates, key=lambda c: c[0])
    return best[0], f"halaman {best[2]}, raw='{best[1]}'"


def _handle_press_release(url, tahun):
    """Handle press release BPS: download page -> find PDF -> parse tabel."""
    tahun = int(tahun)

    print(
        f"    [BPS-UDARA] Press release terdeteksi, "
        f"mencari PDF..."
    )

    html = download_html(url, tag="BPS-UDARA")

    pdf_url = find_pdf_url(html)

    if not pdf_url:
        raise RuntimeError(
            "URL PDF tidak ditemukan di halaman press release BPS."
        )

    print(
        f"    [BPS-UDARA] Download PDF: {pdf_url[:100]}..."
    )

    pdf_bytes = download_pdf(pdf_url)

    print(
        f"    [BPS-UDARA] PDF terunduh ({len(pdf_bytes)} bytes), "
        f"memparse tabel..."
    )

    nilai, info = _extract_from_pdf(pdf_bytes, tahun)

    if nilai is None:
        raise RuntimeError(
            f"Jumlah penumpang angkutan udara tahun {tahun} tidak "
            f"ditemukan di PDF press release ({info})."
        )

    print(
        f"    [BPS-UDARA] Penumpang udara {tahun} = {nilai:,.0f} jiwa "
        f"({info})"
    )

    return float(nilai)


def _extract_penumpang_value(html, tahun):
    """
    Cari data jumlah penumpang angkutan udara dari JSON yang tertanam
    di halaman BPS.

    Return (nilai, label) atau (None, alasan).
    """
    lowered = html.lower()
    candidates = []

    for match in re.finditer(
        r"penumpang.{0,30}(?:angkutan|udara|penerbangan)",
        lowered,
    ):
        segment = html[match.start():match.start() + 900]
        raw = extract_value_near(segment)

        if not raw:
            continue

        nilai = parse_number_id(raw)

        if nilai is None:
            continue

        candidates.append((nilai, raw))

    for match in re.finditer(r"jumlah.{0,20}penumpang", lowered):
        tail = lowered[match.start():match.start() + 60]

        if "angkutan" not in tail and "udara" not in tail:
            continue

        segment = html[match.start():match.start() + 900]
        raw = extract_value_near(segment)

        if not raw:
            continue

        nilai = parse_number_id(raw)

        if nilai is None:
            continue

        if (nilai, raw) not in candidates:
            candidates.append((nilai, raw))

    for nilai, raw in candidates:
        if PENUMPANG_MIN <= nilai <= PENUMPANG_MAX:
            return nilai, raw

    return None, (
        f"kandidat tidak masuk rentang wajar "
        f"{PENUMPANG_MIN:,.0f}-{PENUMPANG_MAX:,.0f}: "
        f"{candidates}" if candidates else "label penumpang tidak ditemukan"
    )


def _handle_data_table(url, tahun):
    """Handle tabel data BPS: download page -> parse JSON embedded."""
    tahun = int(tahun)

    target_url = with_year_param(url, tahun)

    print(
        f"    [BPS-UDARA] Mengunduh tabel penumpang udara tahun {tahun}: "
        f"{target_url[:110]}"
    )

    html = download_html(target_url, tag="BPS-UDARA")

    table_year = extract_table_year(html)

    if table_year is not None and table_year != tahun:
        available = extract_available_years(html)

        raise RuntimeError(
            f"Tabel BPS yang diterima untuk tahun {table_year}, "
            f"bukan {tahun}. Tahun tersedia: "
            f"{available or 'tidak diketahui'}."
        )

    nilai, info = _extract_penumpang_value(html, tahun)

    if nilai is None:
        raise RuntimeError(
            f"Jumlah penumpang angkutan udara tahun {tahun} tidak "
            f"ditemukan di halaman BPS ({info})."
        )

    print(
        f"    [BPS-UDARA] Penumpang udara {tahun} = {info} jiwa "
        f"(tahun_data={table_year})"
    )

    return float(nilai)


def scrape_penumpang_udara(url, tahun, description=None, wilayah=None):
    """
    Return jumlah penumpang angkutan udara (jiwa) untuk satu tahun.

    Mendukung:
    - Press release BPS (/pressrelease/...) -> download & parse PDF
    - Tabel data BPS (/statistics-table/...) -> parse JSON embedded
    """
    tahun = int(tahun)

    cache_key = (str(url or "").strip(), tahun)

    if cache_key in _CACHE:
        return _CACHE[cache_key]

    if is_press_release(url):
        nilai = _handle_press_release(url, tahun)
    else:
        nilai = _handle_data_table(url, tahun)

    _CACHE[cache_key] = nilai

    return nilai


__all__ = [
    "scrape_penumpang_udara",
]
