"""
Generic Auto Extractor — extractor otomatis untuk URL yang tidak punya adapter.

HANYA digunakan sebagai FALLBACK terakhir ketika tidak ada adapter khusus.
Confidence sengaja dibatasi rendah (maks 0.65) karena hasilnya perlu review.

Cara pakai:
    from sources.generic_auto_extractor import try_generic_auto
    result = try_generic_auto(url, tahun, description, wilayah)
"""

import json
import re

from sources.bps_utils import (
    detect_rentang,
    download_html,
    extract_valid_metric_terms,
    parse_number_id,
    METRIC_SYNONYMS,
)

_CACHE = {}

# Confidence maksimal untuk generic extractor — selalu perlu review.
MAX_GENERIC_CONFIDENCE = 0.65

# Minimum angka yang dianggap sebagai data statistik (bukan kode/nomor).
MIN_STATISTICAL_VALUE = 10


def _year_values(value):
    """Ambil tahun eksplisit dari label atau nilai sumber."""
    return {
        int(match)
        for match in re.findall(
            r"(?<!\d)(20\d{2})(?!\d)",
            str(value or ""),
        )
    }


def _is_likely_data_value(nilai, context=""):
    """Validasi apakah angka kemungkinan besar adalah data statistik."""
    # Angka terlalu kecil untuk data statistik.
    if nilai < MIN_STATISTICAL_VALUE:
        return False

    # Angka tahun — bukan data.
    if 1900 <= nilai <= 2099:
        return False

    # Angka bulat kecil (1-99) di konteks yang salah — kemungkinan nomor urut.
    if nilai < 100 and context:
        # Cek apakah konteks menunjukkan ini adalah data.
        context_lower = context.lower()
        data_indicators = [
            "produksi", "jumlah", "volume", "total", "nilai", "persen",
            "indeks", "rasio", "laju", "angka", "kadar", "proporsi",
            "kontribusi", "pertumbuhan", "perubahan",
        ]
        has_data_context = any(ind in context_lower for ind in data_indicators)
        if not has_data_context:
            return False

    return True


def _extract_from_html_tables(html, metric_terms, rentang, tahun, wilayah):
    """Extract data dari tabel HTML di halaman web."""
    candidates = []

    table_pattern = re.compile(r'<table[^>]*>(.*?)</table>', re.DOTALL | re.IGNORECASE)
    tables = table_pattern.findall(html)

    for table_html in tables:
        row_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
        rows = row_pattern.findall(table_html)

        if len(rows) < 2:
            continue

        header_cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', rows[0], re.DOTALL | re.IGNORECASE)
        header_text = " ".join(re.sub(r'<[^>]+>', '', cell or "").lower() for cell in header_cells)

        header_years = _year_values(header_text)
        if header_years and int(tahun) not in header_years:
            continue

        year_indexes = {
            index
            for index, cell in enumerate(header_cells)
            if int(tahun) in _year_values(
                re.sub(r'<[^>]+>', '', cell or "")
            )
        }

        header_score = 0
        for term in metric_terms:
            if term in header_text:
                header_score += 1.0
            else:
                synonyms = METRIC_SYNONYMS.get(term, [])
                if any(syn in header_text for syn in synonyms):
                    header_score += 0.7

        # Header harus match minimal 1 metric term.
        if header_score < 0.5:
            continue

        for row_html in rows[1:]:
            cells = re.findall(r'<t[hd][^>]*>(.*?)</t[hd]>', row_html, re.DOTALL | re.IGNORECASE)
            if len(cells) < 2:
                continue

            first_cell = re.sub(r'<[^>]+>', '', cells[0] or "").strip().lower()

            row_years = _year_values(first_cell)
            if row_years and int(tahun) not in row_years:
                continue

            if first_cell in ("", "bulan", "periode", "tahun", "wilayah", "provinsi"):
                continue

            wilayah_score = 0
            if wilayah:
                wilayah_lower = wilayah.lower()
                if wilayah_lower in first_cell or first_cell in wilayah_lower:
                    wilayah_score = 2.0
                elif any(part in first_cell for part in wilayah_lower.split() if len(part) > 3):
                    wilayah_score = 1.0

            candidate_cells = (
                [
                    cells[index]
                    for index in sorted(year_indexes)
                    if index < len(cells)
                ]
                if year_indexes
                else cells[1:]
            )

            for cell in candidate_cells:
                raw = re.sub(r'<[^>]+>', '', cell or "").strip()
                if not raw:
                    continue

                nilai = parse_number_id(raw)
                if nilai is None:
                    continue

                if not _is_likely_data_value(nilai, header_text + " " + first_cell):
                    continue

                if rentang[0] <= nilai <= rentang[1]:
                    total_score = header_score + wilayah_score
                    candidates.append((nilai, total_score, "html_table", header_text))

    return candidates


def _extract_from_json_data(data, metric_terms, rentang, tahun, wilayah, depth=0):
    """Recursive search di JSON untuk menemukan data yang cocok."""
    if depth > 10:
        return []

    candidates = []

    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                for row in val:
                    if not isinstance(row, dict):
                        continue

                    row_years = set()
                    for key, value in row.items():
                        key_lower = str(key).lower()
                        if any(
                            hint in key_lower
                            for hint in ("tahun", "year", "periode", "period")
                        ):
                            row_years.update(_year_values(value))

                    if row_years and int(tahun) not in row_years:
                        continue

                    for col_key, col_val in row.items():
                        if isinstance(col_val, bool):
                            continue

                        if not isinstance(col_val, (int, float)):
                            col_val = parse_number_id(str(col_val))
                            if col_val is None:
                                continue

                        if col_val <= 0:
                            continue

                        col_lower = col_key.lower()

                        metric_score = 0
                        for term in metric_terms:
                            if term in col_lower:
                                metric_score += 1.0
                            else:
                                synonyms = METRIC_SYNONYMS.get(term, [])
                                if any(syn in col_lower for syn in synonyms):
                                    metric_score += 0.7

                        # Kolom harus match minimal 1 metric term.
                        if metric_score < 0.5:
                            continue

                        wilayah_score = 0
                        if wilayah:
                            wilayah_lower = wilayah.lower()
                            row_str = str(row.values()).lower()
                            if wilayah_lower in row_str:
                                wilayah_score = 2.0

                        total_score = metric_score + wilayah_score
                        if rentang[0] <= col_val <= rentang[1]:
                            candidates.append((col_val, total_score, "json_data", key))

        for key, val in data.items():
            if key.startswith("__"):
                continue
            candidates.extend(_extract_from_json_data(val, metric_terms, rentang, tahun, wilayah, depth + 1))

    elif isinstance(data, list):
        for item in data:
            candidates.extend(_extract_from_json_data(item, metric_terms, rentang, tahun, wilayah, depth + 1))

    return candidates


def _extract_from_value_raw(html, metric_terms, rentang):
    """Extract value_raw dari HTML dengan context scoring ketat."""
    candidates = []

    for match in re.finditer(
        r'value_raw\D{0,30}(\d[\d., ]*\d|\d)',
        html,
    ):
        raw = match.group(1)
        nilai = parse_number_id(raw)

        if nilai is None:
            continue

        if not _is_likely_data_value(nilai):
            continue

        start = max(0, match.start() - 300)
        end = min(len(html), match.end() + 300)
        context = html[start:end].lower()

        context_score = 0
        for term in metric_terms:
            if term in context:
                context_score += 1.0
            else:
                synonyms = METRIC_SYNONYMS.get(term, [])
                if any(syn in context for syn in synonyms):
                    context_score += 0.7

        if metric_terms:
            context_score /= max(len(metric_terms), 1)

        # Harus ada metric term di konteks.
        if context_score < 0.3:
            continue

        if rentang[0] <= nilai <= rentang[1]:
            candidates.append((nilai, context_score, "value_raw", ""))

    return candidates


def _extract_from_next_data(html, metric_terms, rentang, tahun, wilayah):
    """Extract data dari blok __NEXT_DATA__ (Next.js)."""
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        return []

    try:
        data = json.loads(match.group(1))
        return _extract_from_json_data(data, metric_terms, rentang, tahun, wilayah)
    except (json.JSONDecodeError, ValueError):
        return []


def _extract_from_script_json(html, metric_terms, rentang, tahun, wilayah):
    """Extract data dari JSON yang tertanam di <script> tags."""
    candidates = []

    for match in re.finditer(
        r'<script[^>]*>(.*?)</script>',
        html,
        re.DOTALL | re.IGNORECASE,
    ):
        script_content = match.group(1)

        for json_match in re.finditer(r'\{[^{}]{50,}\}', script_content):
            try:
                data = json.loads(json_match.group(0))
                candidates.extend(
                    _extract_from_json_data(data, metric_terms, rentang, tahun, wilayah)
                )
            except (json.JSONDecodeError, ValueError):
                continue

    return candidates


def _extract_from_text_patterns(html, metric_terms, rentang, tahun):
    """Extract data dari pola text di HTML (fallback terakhir)."""
    candidates = []
    lowered = html.lower()

    for term in metric_terms:
        if len(term) < 4:
            continue

        for match in re.finditer(re.escape(term), lowered):
            tail = html[match.start():match.start() + 200]
            number_matches = re.findall(r'(\d[\d., ]*\d|\d)', tail)

            for num_str in number_matches:
                nilai = parse_number_id(num_str)
                if nilai is None:
                    continue
                if not _is_likely_data_value(nilai, term):
                    continue
                if rentang[0] <= nilai <= rentang[1]:
                    candidates.append((nilai, 0.8, term, "text_pattern"))

    return candidates


def _best_result(candidates):
    """Pilih kandidat terbaik dari semua kandidat."""
    if not candidates:
        return None

    sorted_candidates = sorted(candidates, key=lambda c: c[1], reverse=True)
    best = sorted_candidates[0]
    return best[0]


def try_generic_auto(url, tahun, description=None, wilayah=None):
    """
    Generic auto extractor — coba extract data dari URL tanpa adapter.

    SELALU return confidence rendah (maks 0.65) — hasil perlu review.
    Return ExtractionResult atau None.
    """
    tahun = int(tahun)

    cache_key = (str(url or "").strip(), tahun, str(description or ""))
    if cache_key in _CACHE:
        from models import ExtractionResult
        return ExtractionResult(
            status="REVIEW",
            value={"__main__": _CACHE[cache_key]},
            confidence=0.50,
            source_type="generic_cached",
            message="Data dari cache (generic, perlu review).",
            evidence=["cached=true"],
        )

    lower_url = str(url or "").lower()

    if not lower_url.startswith(("http://", "https://")):
        return None

    metric_terms = extract_valid_metric_terms(description)

    if not metric_terms:
        print(
            f"    [GENERIC-AUTO] Tidak ada metric term valid "
            f"dari: {description}"
        )
        return None

    rentang = detect_rentang(metric_terms)

    print(
        f"    [GENERIC-AUTO] Mencoba extract: metric={metric_terms}, "
        f"rentang={rentang}, tahun={tahun}"
    )

    try:
        html = download_html(url, tag="GENERIC-AUTO")
    except Exception as error:
        print(f"    [GENERIC-AUTO] Gagal download: {error}")
        return None

    all_candidates = []

    all_candidates.extend(
        _extract_from_next_data(html, metric_terms, rentang, tahun, wilayah)
    )

    all_candidates.extend(
        _extract_from_value_raw(html, metric_terms, rentang)
    )

    all_candidates.extend(
        _extract_from_html_tables(html, metric_terms, rentang, tahun, wilayah)
    )

    all_candidates.extend(
        _extract_from_script_json(html, metric_terms, rentang, tahun, wilayah)
    )

    all_candidates.extend(
        _extract_from_text_patterns(html, metric_terms, rentang, tahun)
    )

    if not all_candidates:
        print(f"    [GENERIC-AUTO] Tidak ditemukan data yang cocok")
        return None

    best_value = _best_result(all_candidates)

    if best_value is None:
        return None

    methods_found = len(set(c[2] for c in all_candidates))
    best_score = max(c[1] for c in all_candidates)

    # Confidence rendah untuk generic extractor — selalu perlu review.
    confidence = min(MAX_GENERIC_CONFIDENCE, 0.30 + methods_found * 0.08 + best_score * 0.10)

    print(
        f"    [GENERIC-AUTO] Ditemukan: {best_value} "
        f"(confidence={confidence:.2f}, methods={methods_found})"
    )

    _CACHE[cache_key] = best_value

    from models import ExtractionResult
    return ExtractionResult(
        status="REVIEW",
        value={"__main__": best_value},
        confidence=confidence,
        source_type="generic_auto",
        message=f"Data dari auto-extract (perlu review): {best_value}",
        evidence=[
            f"methods={methods_found}",
            f"best_score={best_score:.2f}",
            f"candidates={len(all_candidates)}",
        ],
    )


__all__ = ["try_generic_auto"]
