import re
from urllib.parse import urlparse

import requests

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
from sources.registry import AdapterContext, matching_adapter_spec
from sources.bps_utils import (
    extract_table_year,
    parse_number_id,
    with_year_param,
)


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


def _is_brin_url(url):
    """Deteksi domain BRIN untuk menjalankan parser sumber sebelum heuristik."""
    try:
        host = (urlparse(str(url or "")).hostname or "").lower()
        return host == "brin.go.id" or host.endswith(".brin.go.id")
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

    # Gerbang tahun: halaman harus memuat tahun yang diminta,
    # kalau tidak payload bisa tahun lain (default) -> nilai salah.
    page_year = extract_table_year(html)
    if page_year is not None and int(page_year) != int(tahun):
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

    # Gerbang tahun: payload __NEXT_DATA__ juga harus tahun diminta.
    page_year = extract_table_year(html)
    if page_year is not None and int(page_year) != int(tahun):
        return None

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
    cakupan_wilayah=None,
):
    instruction = parse_instruction(
        description,
        default_wilayah=wilayah,
        cakupan_wilayah=cakupan_wilayah,
    )

    # Untuk halaman BPS: selalu bawa ?year=<tahun> supaya payload
    # benar-benar data tahun yang diminta (bukan tahun default).
    fetch_url = url
    if _is_bps_url(url):
        fetch_url = with_year_param(url, tahun)

    # Adapter yang secara eksplisit menandai prefer_direct tidak perlu
    # melewati fetch/deteksi generik terlebih dahulu. Ini menghindari
    # request halaman yang tidak digunakan oleh parser khusus.
    adapter_context = AdapterContext(
        url=url,
        tahun=tahun,
        description=description,
        wilayah=wilayah,
        kode=kode,
        nama_indikator=nama_indikator,
        sumber_2=sumber_2,
        sumber_3=sumber_3,
    )
    preferred_spec = matching_adapter_spec(adapter_context)
    if preferred_spec and preferred_spec.prefer_direct:
        direct_result = try_legacy_source(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
            kode=kode,
            nama_indikator=nama_indikator,
            sumber_2=sumber_2,
            sumber_3=sumber_3,
        )
        if direct_result is not None:
            return _normalize_status(direct_result)

    try:
        payload = fetch_and_detect(fetch_url)
    except requests.RequestException:
        # Pemulihan khusus URL tabel BPS:
        # fetch generik dapat gagal (mis. HTTP 525), tetapi adapter BPS
        # masih dapat mengambil data melalui endpoint/network capture.
        # Jalur ini hanya berlaku untuk URL statistics-table BPS sehingga
        # perilaku sumber non-BPS tetap tidak berubah.
        bps_table_result = None
        if _is_bps_url(url):
            table_path = urlparse(str(url or "")).path.lower()
            is_bps_table = "/statistics-table/" in table_path
            if is_bps_table:
                from sources.bps_table_adapter import try_bps_table

                table_kwargs = {
                    "url": fetch_url,
                    "tahun": tahun,
                    "description": description,
                    "wilayah": wilayah,
                    "nama_indikator": nama_indikator,
                }
                if cakupan_wilayah:
                    table_kwargs["cakupan_wilayah"] = cakupan_wilayah

                try:
                    bps_table_result = try_bps_table(**table_kwargs)
                except requests.RequestException:
                    # Jika adapter juga mengalami kegagalan jaringan,
                    # lanjutkan ke fallback legacy di bawah.
                    bps_table_result = None

                if bps_table_result is not None and bps_table_result.status in {
                    "SUCCESS",
                    "REVIEW",
                    "DATA_TIDAK_ADA",
                }:
                    return _normalize_status(bps_table_result)

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
        if legacy is not None and legacy.status in {
            "SUCCESS",
            "UNAVAILABLE",
            "LINK_TIDAK_LENGKAP",
            "DATA_TIDAK_ADA",
            "SUMBER_PERLU_LOGIN",
            "SUMBER_TERBLOKIR",
        }:
            return legacy

        # Jika adapter BPS memberi diagnosis yang lebih spesifik daripada
        # fetch generik, kembalikan diagnosis tersebut alih-alih kehilangan
        # konteks dengan melempar HTTP error awal.
        if bps_table_result is not None:
            return _normalize_status(bps_table_result)

        raise

    brin_result = None
    if _is_brin_url(url):
        brin_result = try_legacy_source(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
            kode=kode,
            nama_indikator=nama_indikator,
            sumber_2=sumber_2,
            sumber_3=sumber_3,
        )
        if brin_result is not None and brin_result.status in {
            "SUCCESS",
            "UNAVAILABLE",
            "LINK_TIDAK_LENGKAP",
            "DATA_TIDAK_ADA",
            "SUMBER_PERLU_LOGIN",
            "SUMBER_TERBLOKIR",
        }:
            return brin_result

    # Untuk halaman BPS: parser universal tabel dulu, adapter khusus
    # (embedded JSON / __NEXT_DATA__) hanya jadi cadangan NEED_PARSER.
    bps_pdf_result = None
    bps_table_result = None
    if _is_bps_url(url):
        # Universal: baca tabel BPS terstruktur (tahun + kolom +
        # seluruh baris + konversi satuan) sebelum heuristik lama.
        table_path = urlparse(str(url or "")).path.lower()
        is_bps_table_listing = (
            table_path.rstrip("/").endswith("/statistics-table")
        )
        if "/statistics-table/" in table_path or is_bps_table_listing:
            from sources.bps_table_adapter import try_bps_table
            table_kwargs = {
                "url": fetch_url,
                "tahun": tahun,
                "description": description,
                "wilayah": wilayah,
                "nama_indikator": nama_indikator,
            }
            if cakupan_wilayah:
                table_kwargs["cakupan_wilayah"] = cakupan_wilayah
            template_result = try_bps_table(**table_kwargs)
            if template_result is not None:
                if template_result.status in {
                    "SUCCESS",
                    "REVIEW",
                    "DATA_TIDAK_ADA",
                }:
                    return _normalize_status(template_result)
                bps_table_result = template_result

        # Cadangan 1: extract JSON embedded (heuristik lama).
        bps_result = _try_bps_embedded_json(
            payload, instruction, int(tahun)
        )
        if bps_result is not None:
            return _normalize_status(bps_result)

        # Cadangan 2: coba __NEXT_DATA__ (Next.js SPA).
        bps_next = _try_bps_next_data(
            payload, instruction, int(tahun)
        )
        if bps_next is not None:
            return _normalize_status(bps_next)

        if (
            bps_table_result is not None
            and bps_table_result.status == "NEED_PARSER"
            and not is_bps_table_listing
            and payload.kind in {"html", "spa"}
        ):
            browser_result = extract_browser(
                url,
                instruction,
                int(tahun),
                decimal=",",
                thousands=".",
                select_year=int(tahun),
            )
            if browser_result.status == "SUCCESS":
                return _normalize_status(browser_result)
            if browser_result.message:
                bps_table_result.message = (
                    f"{bps_table_result.message} | Browser fallback: "
                    f"{browser_result.message}"
                )
            bps_table_result.evidence.extend(browser_result.evidence)

        if bps_table_result is not None and not is_bps_table_listing:
            return _normalize_status(bps_table_result)

        # Coba konektor PDF pada semua halaman BPS, bukan berdasarkan
        # pola path tertentu seperti /pressrelease/.
        if payload.kind in {"html", "spa"}:
            from sources.bps_pdf_adapter import try_bps_pdf
            bps_pdf_result = try_bps_pdf(
                url=url,
                tahun=tahun,
                description=description,
                wilayah=wilayah,
                nama_indikator=nama_indikator,
                page_html=payload.content.decode("utf-8", "ignore"),
                page_kind=payload.kind,
            )
            if (
                bps_pdf_result is not None
                and bps_pdf_result.status in {"SUCCESS", "REVIEW"}
            ):
                return _normalize_status(bps_pdf_result)
            if (
                bps_pdf_result is not None
                and any(
                    evidence.startswith("pdf_candidates=")
                    for evidence in bps_pdf_result.evidence
                )
                and (
                    "pdf_candidates=0" not in bps_pdf_result.evidence
                    or _is_bps_pdf_listing(url)
                )
            ):
                return _normalize_status(bps_pdf_result)

        # Fallback 5: render SPA dengan Selenium untuk BPS pages.
        if payload.kind == "spa":
            browser_result = extract_browser(
                fetch_url, instruction, int(tahun)
            )
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
            url=fetch_url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
        )
        if auto_result is not None:
            auto_result = _normalize_status(auto_result)
            if auto_result.status != "NEED_PARSER":
                return auto_result
            if auto_result.message:
                generic.message = (
                    f"{generic.message} | Generic auto: "
                    f"{auto_result.message}"
                )
            generic.evidence.extend(auto_result.evidence)

    if generic.status == "NEED_PARSER":
        if (
            bps_table_result is not None
            and bps_table_result.status == "NEED_PARSER"
        ):
            if bps_table_result.message:
                generic.message = (
                    f"{generic.message} | BPS TABLE: "
                    f"{bps_table_result.message}"
                )
            generic.evidence.extend(bps_table_result.evidence)
            generic.source_type = bps_table_result.source_type

        if (
            bps_pdf_result is not None
            and bps_pdf_result.status == "NEED_PARSER"
        ):
            if bps_pdf_result.message:
                generic.message = (
                    f"{generic.message} | BPS PDF: "
                    f"{bps_pdf_result.message}"
                )
            generic.evidence.extend(bps_pdf_result.evidence)
            if bps_table_result is None:
                generic.source_type = "bps_pdf"

    if generic.status == "NEED_PARSER":
        legacy = brin_result or try_legacy_source(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
            kode=kode,
            nama_indikator=nama_indikator,
            sumber_2=sumber_2,
            sumber_3=sumber_3,
        )
        if legacy is not None and legacy.status in {
            "SUCCESS",
            "UNAVAILABLE",
            "LINK_TIDAK_LENGKAP",
            "DATA_TIDAK_ADA",
            "SUMBER_PERLU_LOGIN",
            "SUMBER_TERBLOKIR",
        }:
            return legacy
        if legacy is not None and legacy.status == "LEGACY_FAILED":
            generic.message = (
                f"{generic.message} | Legacy juga gagal: {legacy.message}"
            )

    return generic


def _is_bps_pdf_listing(url):
    path = urlparse(str(url or "")).path.lower()
    return any(
        marker in path
        for marker in (
            "/pressrelease",
            "/publication",
            "/publikasi",
            "/news",
            "/berita",
            "/subject",
            "/artikel",
        )
    ) and not path.endswith((".html", ".htm"))