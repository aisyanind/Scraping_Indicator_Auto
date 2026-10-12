import re
from io import BytesIO

import pdfplumber

from sources.bps_utils import (
    detect_rentang,
    download_html,
    download_pdf,
    extract_metric_terms,
    find_pdf_url,
    parse_number_id,
    METRIC_SYNONYMS,
)

_CACHE = {}

# Kata-kata umum arah/lokalitas yang terlalu generik untuk partial matching wilayah.
_GENERIC_LOCATION_WORDS = {
    "utara", "selatan", "timur", "barat", "tengah",
    "indonesia", "nusantara",
}


def _detect_year_columns(table):
    """Deteksi kolom tahun dari header tabel. Return {tahun_int: col_index}."""
    year_cols = {}

    # Scan up to 4 rows untuk multi-row headers.
    for row_idx in range(min(4, len(table))):
        row = table[row_idx]
        if not row:
            continue
        for idx, cell in enumerate(row):
            cell_str = str(cell or "").strip()

            # Exact match: cell is just "2021"
            m = re.match(r"^(20\d{2})$", cell_str)
            if m:
                year_cols[int(m.group(1))] = idx
                continue

            # Embedded match: year is part of cell text
            # e.g. "Tahun 2021", "2021*", "2021 (Juta US$)"
            years_found = re.findall(r'\b(20\d{2})\b', cell_str)
            if len(years_found) == 1 and int(years_found[0]) not in year_cols:
                year_cols[int(years_found[0])] = idx

    return year_cols


def _extract_from_pdf(pdf_bytes, metric_terms, rentang, tahun, wilayah=None):
    """
    Parse tabel dari PDF BPS, cari data yang match metric_terms + wilayah + tahun.

    Return (nilai, info) atau (None, alasan).
    """
    candidates = []
    target_lower = (wilayah or "").lower()

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page_num, page in enumerate(pdf.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                continue

            text_lower = text.lower()

            is_relevant = any(
                term in text_lower or any(syn in text_lower for syn in METRIC_SYNONYMS.get(term, []))
                for term in metric_terms
            )

            if not is_relevant and metric_terms:
                continue

            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []

            for table in tables:
                if not table or len(table) < 2:
                    continue

                header_text = " ".join(
                    str(cell or "").lower()
                    for cell in table[0]
                )

                header_match = any(
                    term in header_text
                    or any(syn in header_text for syn in METRIC_SYNONYMS.get(term, []))
                    for term in metric_terms
                )

                if not header_match and metric_terms:
                    if len(table) > 1:
                        alt_header = " ".join(
                            str(cell or "").lower()
                            for cell in table[1]
                        )
                        header_match = any(
                            term in alt_header
                            or any(syn in alt_header for syn in METRIC_SYNONYMS.get(term, []))
                            for term in metric_terms
                        )

                # Deteksi kolom tahun.
                year_cols = _detect_year_columns(table)
                use_year_col = bool(year_cols) and tahun in year_cols

                # GATE: skip tabel jika tahun tidak bisa dikonfirmasi
                if not use_year_col:
                    if year_cols:
                        # Kolom tahun terdeteksi, tapi tahun target TIDAK ada
                        # → tabel ini punya data tahun lain
                        continue
                    # Tidak ada kolom tahun. Cek teks halaman:
                    # terima jika tahun target ada di halaman.
                    page_years = sorted(set(
                        int(y) for y in re.findall(r'\b(20\d{2})\b', text)
                    ))
                    if tahun in page_years:
                        pass  # terima
                    else:
                        continue  # skip — tahun tidak ada di halaman

                for row in table:
                    if not row:
                        continue

                    first_cell = str(row[0] or "").strip().lower()

                    if first_cell in ("bulan", "periode", "komoditas", "wilayah", ""):
                        continue

                    # Score wilayah.
                    wilayah_score = 0
                    if target_lower:
                        if target_lower in first_cell or first_cell in target_lower:
                            wilayah_score = 3.0
                        elif any(
                            part in first_cell
                            for part in target_lower.split()
                            if len(part) > 3 and part not in _GENERIC_LOCATION_WORDS
                        ):
                            wilayah_score = 1.5

                    # Jika ada kolom tahun, hanya ambil dari kolom yang sesuai.
                    if use_year_col:
                        col_idx = year_cols[tahun]
                        cells_to_check = [(col_idx, row[col_idx] if col_idx < len(row) else None)]
                    else:
                        cells_to_check = list(enumerate(row[1:], start=1))

                    for col_idx, cell in cells_to_check:
                        raw = str(cell or "").strip()
                        if not raw:
                            continue

                        nilai = parse_number_id(raw)
                        if nilai is None:
                            continue

                        if 1900 <= nilai <= 2099:
                            continue

                        if rentang[0] <= nilai <= rentang[1]:
                            candidates.append(
                                (nilai, raw, page_num, first_cell, header_match, wilayah_score)
                            )

    if not candidates:
        return None, f"tabel tidak ditemukan di PDF (metric={metric_terms})"

    # Prioritas: wilayah_score tertinggi, lalu header_match.
    matched = [c for c in candidates if c[4]]
    if matched:
        if target_lower:
            wilayah_matched = [c for c in matched if c[5] > 0]
            if wilayah_matched:
                best = max(wilayah_matched, key=lambda c: c[5])
            else:
                # Cek apakah tabel ini punya label wilayah sama sekali.
                # Jika tidak ada satupun → tabel satu wilayah (press release daerah)
                any_region = any(c[5] > 0 for c in candidates)
                if not any_region:
                    best = matched[0]
                else:
                    return None, f"tidak ada data untuk wilayah '{wilayah}' di PDF"
        else:
            best = matched[0]
    else:
        if target_lower:
            wilayah_matched = [c for c in candidates if c[5] > 0]
            if wilayah_matched:
                best = max(wilayah_matched, key=lambda c: c[5])
            else:
                any_region = any(c[5] > 0 for c in candidates)
                if not any_region:
                    best = candidates[0]
                else:
                    return None, f"tidak ada data untuk wilayah '{wilayah}' di PDF"
        else:
            best = candidates[0]

    year_info = f", tahun_col={tahun}" if use_year_col else ", tahun_col=PAGE_VERIFIED"
    return best[0], (
        f"halaman {best[2]}, raw='{best[1]}', "
        f"label='{best[3]}', header_match={best[4]}, "
        f"wilayah_score={best[5]}{year_info}"
    )


def try_bps_pdf(url, tahun, description=None, wilayah=None):
    """
    Template generic untuk halaman BPS yang mengandung link PDF.

    Bisa handle: /pressrelease/, /subject/, atau halaman BPS lainnya.
    Return ExtractionResult atau None.
    """
    tahun = int(tahun)
    lower_url = str(url or "").lower()

    if "bps.go.id" not in lower_url:
        return None

    cache_key = (str(url or "").strip(), tahun, str(description or ""))
    if cache_key in _CACHE:
        from models import ExtractionResult
        return ExtractionResult(
            status="SUCCESS",
            value={"__main__": _CACHE[cache_key]},
            confidence=0.95,
            source_type="bps_pdf_cached",
            message="Data dari cache.",
            evidence=["cached=true"],
        )

    metric_terms = extract_metric_terms(description)
    rentang = detect_rentang(metric_terms)

    print(
        f"    [BPS-PDF] Template: metric={metric_terms}, "
        f"rentang={rentang}, tahun={tahun}"
    )

    try:
        html = download_html(url, tag="BPS-PDF")
    except Exception as error:
        from models import ExtractionResult
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_pdf",
            message=f"Gagal download: {str(error)[:200]}",
            evidence=[],
        )

    pdf_url = find_pdf_url(html)
    if not pdf_url:
        from models import ExtractionResult
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_pdf",
            message="URL PDF tidak ditemukan di halaman press release.",
            evidence=[],
        )

    print(f"    [BPS-PDF] Download PDF: {pdf_url[:100]}...")

    try:
        pdf_bytes = download_pdf(pdf_url)
    except Exception as error:
        from models import ExtractionResult
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_pdf",
            message=f"Gagal download PDF: {str(error)[:200]}",
            evidence=[],
        )

    print(f"    [BPS-PDF] PDF terunduh ({len(pdf_bytes)} bytes), memparse...")

    nilai, info = _extract_from_pdf(pdf_bytes, metric_terms, rentang, tahun, wilayah)

    if nilai is None:
        from models import ExtractionResult
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_pdf",
            message=f"Data tidak ditemukan di PDF ({info})",
            evidence=[],
        )

    print(f"    [BPS-PDF] Nilai = {nilai} ({info})")

    _CACHE[cache_key] = nilai

    from models import ExtractionResult
    return ExtractionResult(
        status="SUCCESS",
        value={"__main__": nilai},
        confidence=0.85,
        source_type="bps_pdf",
        message=f"Data dari PDF BPS: {nilai}",
        evidence=[f"pdf_value={nilai}", f"info={info}"],
    )


__all__ = ["try_bps_pdf"]
