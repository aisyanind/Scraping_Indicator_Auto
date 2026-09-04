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

    if (
        generic.status == "NEED_PARSER"
        and legacy is not None
        and legacy.status == "LEGACY_FAILED"
    ):
        generic.message = (
            f"{generic.message} | Legacy juga gagal: {legacy.message}"
        )

    return generic
