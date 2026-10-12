import re
from urllib.parse import urlparse

from config import (
    AUTO_CONFIDENCE_THRESHOLD,
    REVIEW_CONFIDENCE_THRESHOLD,
)
from engine.detector import fetch_and_detect
from engine.instructions import parse_instruction
from extractors.browser import extract_browser
from extractors.csv import extract_csv
from extractors.discovery import extract_discovery
from extractors.excel import extract_excel
from extractors.html import extract_html
from extractors.json_api import extract_json
from extractors.pdf import extract_pdf
from extractors.zip import extract_zip
from models import (
    STATUS_NEED_PARSER,
    STATUS_REVIEW,
    STATUS_SUKSES,
    STATUS_TANPA_NILAI,
    ExtractionResult,
)
from sources.legacy_bridge import try_legacy_source
from sources.bps_utils import parse_number_id


EXTRACTORS = {
    "pdf": extract_pdf,
    "excel": extract_excel,
    "csv": extract_csv,
    "html": extract_html,
    "json": extract_json,
    "zip": extract_zip,
    "spa": extract_discovery,
}


def run_generic(payload, instruction, tahun):
    """
    Jalankan extractor sesuai kind payload (tanpa fallback browser).
    Dipakai oleh discovery untuk memproses sub-link.
    """
    extractor = EXTRACTORS.get(payload.kind)

    if extractor:
        return extractor(
            payload,
            instruction,
            int(tahun),
        )

    return extract_browser(
        payload.final_url,
        instruction,
        int(tahun),
    )


def _normalize_status(result: ExtractionResult) -> ExtractionResult:
    # Status semantik dari adapter khusus tidak boleh ditimpa.
    if result.status in STATUS_TANPA_NILAI:
        return result

    if result.status == STATUS_NEED_PARSER:
        return result

    if result.confidence >= AUTO_CONFIDENCE_THRESHOLD:
        result.status = STATUS_SUKSES
    elif result.confidence >= REVIEW_CONFIDENCE_THRESHOLD:
        result.status = STATUS_REVIEW
    else:
        result.status = STATUS_NEED_PARSER

    return result


def _is_bps_url(url):
    """Deteksi apakah URL dari domain BPS."""
    try:
        host = urlparse(str(url or "")).hostname or ""
        return "bps.go.id" in host
    except Exception:
        return False


def _try_bps_embedded_json(payload, instruction, tahun):
    """
    Untuk halaman BPS tabel baru yang menyematkan data sebagai JSON
    di dalam HTML. Coba extract value_raw dari JSON tanpa perlu
    Selenium/SPA extractor.

    Strategi:
    1. Extract setiap value_raw BESERTA konteks di sekitarnya.
    2. Score konteks berdasarkan kecocokan dengan metric_terms.
    3. Ambil nilai dengan skor konteks tertinggi.
    4. Fallback: max() untuk tabel agregat (PDRB), atau nilai
       pertama yang wajar untuk tabel indeks (NTP).

    Return ExtractionResult atau None jika tidak ditemukan.
    """
    if not _is_bps_url(payload.url):
        return None

    if payload.kind not in ("html", "spa"):
        return None

    html = payload.content.decode("utf-8", "ignore")
    lowered = html.lower()

    if "value_raw" not in html and "tahun_data" not in html:
        return None

    # Ekstrak value_raw BESERTA konteks di sekitarnya.
    candidates = []
    for match in re.finditer(
        r'value_raw\D{0,30}(\d[\d., ]*\d|\d)',
        html,
    ):
        raw = match.group(1)
        nilai = parse_number_id(raw)

        if nilai is None:
            continue

        # Ambil konteks 300 char sebelum dan sesudah.
        start = max(0, match.start() - 300)
        end = min(len(html), match.end() + 300)
        context = html[start:end].lower()

        # Score kecocokan konteks dengan metric_terms.
        context_score = 0
        for term in instruction.metric_terms:
            if len(term) < 3:
                continue
            if term in context:
                context_score += 1.0
            else:
                # Cek sinonim.
                from engine.tabular import METRIC_SYNONYMS
                synonyms = METRIC_SYNONYMS.get(term, [])
                if any(syn in context for syn in synonyms):
                    context_score += 0.7

        if instruction.metric_terms:
            context_score /= max(len(instruction.metric_terms), 1)

        candidates.append((nilai, context_score, context[:100]))

    if not candidates:
        return None

    # Strategy 1: Jika ada kandidat dengan context_score tinggi, ambil itu.
    best_by_context = max(candidates, key=lambda c: c[1])

    if best_by_context[1] >= 0.5 and best_by_context[0] > 0:
        best_value = best_by_context[0]
        confidence = 0.85
    else:
        # Strategy 2: Fallback — ambil max() untuk tabel agregat.
        all_values = [c[0] for c in candidates if c[0] > 0]
        if not all_values:
            return None
        best_value = max(all_values)
        confidence = 0.70

    has_metric = any(
        term in lowered
        for term in instruction.metric_terms
        if len(term) >= 3
    )

    if has_metric:
        confidence = min(1.0, confidence + 0.05)

    return ExtractionResult(
        status="SUCCESS",
        value={instruction.target_wilayahs[0]: best_value},
        confidence=confidence,
        source_type="bps_json",
        message="Data dari JSON embedded BPS.",
        evidence=[
            f"embedded_json_values={len(candidates)}",
            f"best_context_score={best_by_context[1]:.2f}",
        ],
    )


def _try_bps_next_data(payload, instruction, tahun):
    """
    Untuk halaman BPS yang menggunakan Next.js. Data sering disimpan
    di blok __NEXT_DATA__ sebagai JSON terstruktur.

    Return ExtractionResult atau None.
    """
    if not _is_bps_url(payload.url):
        return None

    if payload.kind not in ("html", "spa"):
        return None

    html = payload.content.decode("utf-8", "ignore")

    # Cari blok __NEXT_DATA__.
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        return None

    try:
        import json
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None

    # Cari tabel data di dalam props.
    table_data = _extract_table_from_next(data, instruction, tahun)
    if table_data is None:
        return None

    best_value, evidence = table_data
    return ExtractionResult(
        status="SUCCESS",
        value={instruction.target_wilayahs[0]: best_value},
        confidence=0.82,
        source_type="bps_nextjs",
        message="Data dari __NEXT_DATA__ BPS.",
        evidence=evidence,
    )


def _extract_table_from_next(data, instruction, tahun):
    """Recursive search di __NEXT_DATA__ untuk menemukan tabel data."""
    if isinstance(data, dict):
        # Cek apakah ada array of objects dengan key numeric/nilai.
        if "tableData" in data:
            return _pick_best_from_table(
                data["tableData"], instruction, tahun
            )
        if "data" in data and isinstance(data["data"], list):
            return _pick_best_from_table(
                data["data"], instruction, tahun
            )

        # Recursive search.
        for key, val in data.items():
            if key.startswith("__"):
                continue
            result = _extract_table_from_next(val, instruction, tahun)
            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = _extract_table_from_next(item, instruction, tahun)
            if result is not None:
                return result

    return None


def _pick_best_from_table(rows, instruction, tahun):
    """Pilih baris terbaik dari tabel berdasarkan metric_terms."""
    if not rows or not isinstance(rows, list):
        return None

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        # Cari angka di semua kolom.
        for key, val in row.items():
            if not isinstance(val, (int, float)):
                continue
            if val <= 0:
                continue

            # Score berdasarkan kecocokan key dengan metric_terms.
            key_lower = key.lower()
            score = 0
            for term in instruction.metric_terms:
                if len(term) < 3:
                    continue
                if term in key_lower:
                    score += 1.0
                else:
                    from engine.tabular import METRIC_SYNONYMS
                    synonyms = METRIC_SYNONYMS.get(term, [])
                    if any(syn in key_lower for syn in synonyms):
                        score += 0.7

            candidates.append((val, score, key))

    if not candidates:
        return None

    # Ambil kandidat dengan skor tertinggi.
    best = max(candidates, key=lambda c: c[1])
    evidence = [f"nextjs_col={best[2]}", f"nextjs_score={best[1]:.2f}"]
    return best[0], evidence


def scrape_source_auto(
    url,
    tahun,
    description,
    wilayah,
    kode=None,
    nama_indikator=None,
    sumber_2=None,
    sumber_3=None,
):
    instruction = parse_instruction(
        description,
        default_wilayah=wilayah,
    )

    legacy = try_legacy_source(
        url=url,
        tahun=tahun,
        description=description,
        wilayah=wilayah,
        kode=kode,
        nama_indikator=nama_indikator,
        sumber_2=sumber_2,
        sumber_3=sumber_3,
    )

    if (
        legacy is not None
        and legacy.status in {
            "SUCCESS",
            "UNAVAILABLE",
            "LINK_TIDAK_LENGKAP",
            "DATA_TIDAK_ADA",
            "SUMBER_PERLU_LOGIN",
        }
    ):
        return legacy

    payload = fetch_and_detect(url)

    # Untuk halaman BPS: coba extract JSON embedded dulu sebelum
    # generic extractor (lebih cepat, lebih akurat).
    if _is_bps_url(url):
        bps_result = _try_bps_embedded_json(
            payload, instruction, int(tahun)
        )
        if bps_result is not None:
            return _normalize_status(bps_result)

        # Fallback 2: coba __NEXT_DATA__ (Next.js SPA).
        bps_next = _try_bps_next_data(
            payload, instruction, int(tahun)
        )
        if bps_next is not None:
            return _normalize_status(bps_next)

        # Fallback 3: template adapter (generic untuk semua tabel BPS).
        lower_url = str(url or "").lower()
        if "/statistics-table/" in lower_url:
            from sources.bps_table_adapter import try_bps_table
            template_result = try_bps_table(
                url=url,
                tahun=tahun,
                description=description,
                wilayah=wilayah,
            )
            if template_result is not None:
                return _normalize_status(template_result)

        # Fallback 4: template PDF (generic untuk press release BPS).
        if "/pressrelease/" in lower_url:
            from sources.bps_pdf_adapter import try_bps_pdf
            template_result = try_bps_pdf(
                url=url,
                tahun=tahun,
                description=description,
                wilayah=wilayah,
            )
            if template_result is not None:
                return _normalize_status(template_result)

        # Fallback 5: render SPA dengan Selenium untuk BPS pages.
        if payload.kind == "spa":
            browser_result = extract_browser(url, instruction, int(tahun))
            if browser_result.status == "SUCCESS":
                return _normalize_status(browser_result)

    extractor = EXTRACTORS.get(payload.kind)

    if extractor:
        generic = extractor(
            payload,
            instruction,
            int(tahun),
        )
    else:
        generic = extract_browser(
            payload.final_url,
            instruction,
            int(tahun),
        )

    generic = _normalize_status(generic)

    # Halaman HTML biasa sering hanya menjadi katalog/viewer yang memuat
    # link PDF atau endpoint data. Beri discovery kesempatan sebelum
    # fallback generic yang hanya membaca isi HTML langsung.
    if generic.status == "NEED_PARSER" and payload.kind == "html":
        discovered = extract_discovery(
            payload,
            instruction,
            int(tahun),
        )
        if discovered.status in {"SUCCESS", "REVIEW"}:
            return _normalize_status(discovered)

    # Fallback terakhir: generic auto extractor untuk URL manapun.
    if generic.status == "NEED_PARSER" and payload.kind in {"html", "spa"}:
        from sources.generic_auto_extractor import try_generic_auto
        auto_result = try_generic_auto(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
        )
        if auto_result is not None:
            return _normalize_status(auto_result)

    if (
        generic.status == "NEED_PARSER"
        and legacy is not None
        and legacy.status == "LEGACY_FAILED"
    ):
        generic.message = (
            f"{generic.message} | Legacy juga gagal: {legacy.message}"
        )

    return generic
