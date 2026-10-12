import json
import re
from io import BytesIO

import pdfplumber

from sources.bps_utils import (
    download_html,
    download_file,
    extract_available_years,
    extract_table_year,
    extract_value_near,
    find_pdf_url,
    is_press_release,
    parse_number_id,
    with_year_param,
    PAGE_REQUEST_HEADERS,
)

# NTP adalah indeks, biasanya 70-150 (di atas 100 = petani untung).
NTP_MIN = 50
NTP_MAX = 200

_CACHE = {}


def _extract_ntp_from_pdf(pdf_bytes, tahun):
    """
    Parse tabel dari PDF BPS, cari data NTP.

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

            is_ntp = (
                "nilai tukar petani" in text_lower
                or "ntp" in text_lower
                or "nilai tukar" in text_lower
            )

            if not is_ntp:
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

                if "ntp" not in header_text and "nilai tukar" not in header_text:
                    continue

                for row in table[1:]:
                    if not row:
                        continue

                    first_cell = str(row[0] or "").strip().lower()

                    if first_cell in ("bulan", "periode", "komoditas", ""):
                        continue

                    for cell in row[1:]:
                        raw = str(cell or "").strip()

                        if not raw:
                            continue

                        nilai = parse_number_id(raw)

                        if nilai is None:
                            continue

                        if 1900 <= nilai <= 2099:
                            continue

                        if NTP_MIN <= nilai <= NTP_MAX:
                            candidates.append(
                                (nilai, raw, page_num, first_cell)
                            )

    if not candidates:
        return None, "tabel NTP tidak ditemukan di PDF"

    best = candidates[0]
    return best[0], f"halaman {best[2]}, raw='{best[1]}', label='{best[3]}'"


def _handle_press_release(url, tahun):
    """Handle press release BPS: download page -> find PDF -> parse tabel."""
    tahun = int(tahun)

    print(
        f"    [BPS-NTP] Press release terdeteksi, "
        f"mencari PDF..."
    )

    html = download_html(url, tag="BPS-NTP")

    pdf_url = find_pdf_url(html)

    if not pdf_url:
        raise RuntimeError(
            "URL PDF tidak ditemukan di halaman press release BPS."
        )

    print(
        f"    [BPS-NTP] Download PDF: {pdf_url[:100]}..."
    )

    pdf_bytes, _ = download_file(pdf_url)

    print(
        f"    [BPS-NTP] PDF terunduh ({len(pdf_bytes)} bytes), "
        f"memparse tabel..."
    )

    nilai, info = _extract_ntp_from_pdf(pdf_bytes, tahun)

    if nilai is None:
        raise RuntimeError(
            f"NTP tahun {tahun} tidak ditemukan di PDF press release "
            f"({info})."
        )

    print(
        f"    [BPS-NTP] NTP {tahun} = {nilai:.2f} "
        f"({info})"
    )

    return float(nilai)


def _extract_ntp_value(html, tahun):
    """
    Cari data NTP dari JSON yang tertanam di halaman BPS.

    Return (nilai, label) atau (None, alasan).
    """
    lowered = html.lower()
    candidates = []

    for match in re.finditer(
        r"(?:nilai.tukar.petani|ntp).{0,50}value_raw",
        lowered,
    ):
        segment = html[match.start():match.start() + 500]
        raw = extract_value_near(segment)

        if not raw:
            continue

        nilai = parse_number_id(raw)

        if nilai is None:
            continue

        candidates.append((nilai, raw, "pattern1"))

    for match in re.finditer(
        r"value_raw\D{0,30}(\d[\d., ]*\d|\d)",
        html,
    ):
        start = max(0, match.start() - 300)
        context = html[start:match.start()].lower()

        is_ntp_context = (
            "ntp" in context
            or "nilai tukar" in context
            or "petani" in context
        )

        if not is_ntp_context:
            continue

        raw = match.group(1)
        nilai = parse_number_id(raw)

        if nilai is None:
            continue

        if (nilai, raw, "pattern2") not in candidates:
            candidates.append((nilai, raw, "pattern2"))

    for nilai, raw, pattern in candidates:
        if NTP_MIN <= nilai <= NTP_MAX:
            return nilai, f"raw='{raw}' ({pattern})"

    return None, (
        f"kandidat tidak masuk rentang wajar "
        f"{NTP_MIN}-{NTP_MAX}: "
        f"{[(c[0], c[2]) for c in candidates]}"
        if candidates else "label NTP tidak ditemukan"
    )


def _extract_from_next_data(data, tahun):
    """Recursive search di __NEXT_DATA__ untuk menemukan nilai NTP."""
    if isinstance(data, dict):
        for key, val in data.items():
            key_lower = key.lower()
            if any(term in key_lower for term in ("ntp", "nilai tukar", "petani")):
                if isinstance(val, (int, float)) and NTP_MIN <= val <= NTP_MAX:
                    return float(val)

        for key, val in data.items():
            if key.startswith("__"):
                continue
            result = _extract_from_next_data(val, tahun)
            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = _extract_from_next_data(item, tahun)
            if result is not None:
                return result

    return None


def _handle_data_table(url, tahun):
    """Handle tabel data BPS: download page -> parse JSON embedded."""
    tahun = int(tahun)

    target_url = with_year_param(url, tahun)

    print(
        f"    [BPS-NTP] Mengunduh tabel NTP tahun {tahun}: "
        f"{target_url[:110]}"
    )

    html = download_html(target_url, tag="BPS-NTP")

    table_year = extract_table_year(html)

    if table_year is not None and table_year != tahun:
        available = extract_available_years(html)

        raise RuntimeError(
            f"Tabel BPS yang diterima untuk tahun {table_year}, "
            f"bukan {tahun}. Tahun tersedia: "
            f"{available or 'tidak diketahui'}."
        )

    next_match = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )

    if next_match:
        try:
            data = json.loads(next_match.group(1))
            nilai = _extract_from_next_data(data, tahun)
            if nilai is not None:
                print(
                    f"    [BPS-NTP] NTP {tahun} = {nilai:.2f} "
                    f"(dari __NEXT_DATA__)"
                )
                return float(nilai)
        except (json.JSONDecodeError, ValueError):
            pass

    nilai, info = _extract_ntp_value(html, tahun)

    if nilai is None:
        raise RuntimeError(
            f"NTP tahun {tahun} tidak ditemukan di halaman BPS "
            f"({info})."
        )

    print(
        f"    [BPS-NTP] NTP {tahun} = {nilai:.2f} "
        f"(tahun_data={table_year}, {info})"
    )

    return float(nilai)


def _handle_download_php(url, tahun):
    """Handle URL download.php BPS: download file, detect type, parse."""
    tahun = int(tahun)

    target_url = with_year_param(url, tahun)

    print(
        f"    [BPS-NTP] Download file: {target_url[:100]}..."
    )

    content, content_type = download_file(target_url)

    print(
        f"    [BPS-NTP] File terunduh ({len(content)} bytes), "
        f"type={content_type}"
    )

    if "pdf" in content_type.lower() or content[:4] == b"%PDF":
        print(f"    [BPS-NTP] File adalah PDF, memparse...")
        nilai, info = _extract_ntp_from_pdf(content, tahun)
        if nilai is not None:
            print(f"    [BPS-NTP] NTP {tahun} = {nilai:.2f} ({info})")
            return float(nilai)

    if "json" in content_type.lower() or content[:1] == b"{":
        try:
            data = json.loads(content.decode("utf-8", "ignore"))
            nilai = _extract_from_next_data(data, tahun)
            if nilai is not None:
                print(f"    [BPS-NTP] NTP {tahun} = {nilai:.2f} (JSON)")
                return float(nilai)
        except (json.JSONDecodeError, ValueError):
            pass

    if "html" in content_type.lower() or b"<html" in content[:1000].lower():
        html = content.decode("utf-8", "ignore")
        nilai, info = _extract_ntp_value(html, tahun)
        if nilai is not None:
            print(f"    [BPS-NTP] NTP {tahun} = {nilai:.2f} ({info})")
            return float(nilai)

    raise RuntimeError(
        f"NTP tahun {tahun} tidak ditemukan di file download BPS "
        f"(type={content_type})."
    )


def try_ntp_source(url, tahun, description=None, wilayah=None):
    """
    Adapter untuk NTP (Nilai Tukar Petani).

    Return ExtractionResult atau None jika URL bukan BPS / gagal.
    """
    tahun = int(tahun)

    cache_key = (str(url or "").strip(), tahun)

    if cache_key in _CACHE:
        from models import ExtractionResult
        return ExtractionResult(
            status="SUCCESS",
            value={"__main__": _CACHE[cache_key]},
            confidence=0.95,
            source_type="ntp_cached",
            message="NTP dari cache.",
            evidence=["cached=true"],
        )

    lower_url = str(url or "").lower()

    if "bps.go.id" not in lower_url:
        return None

    try:
        if "download.php" in lower_url:
            nilai = _handle_download_php(url, tahun)
        elif is_press_release(url):
            nilai = _handle_press_release(url, tahun)
        else:
            nilai = _handle_data_table(url, tahun)

        _CACHE[cache_key] = nilai

        from models import ExtractionResult
        return ExtractionResult(
            status="SUCCESS",
            value={"__main__": nilai},
            confidence=0.90,
            source_type="ntp_adapter",
            message=f"NTP tahun {tahun} = {nilai:.2f}",
            evidence=[f"ntp_value={nilai:.2f}"],
        )

    except Exception as error:
        from models import ExtractionResult
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="ntp_adapter",
            message=f"NTP adapter gagal: {str(error)[:200]}",
            evidence=[f"error={str(error)[:100]}"],
        )


__all__ = [
    "try_ntp_source",
]
