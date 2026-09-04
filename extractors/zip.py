"""
Ekstraktor ZIP — buka arsip, teruskan tiap file data ke extractor yang sesuai.

Sumber yang dibungkus ZIP (mis. Open Data Jabar) akan:
1. Dibuka oleh detector menjadi kind="zip".
2. Di sini di-unzip; file CSV/XLSX/PDF/JSON di dalamnya di-extract.
3. Hasil terbaik (confidence tertinggi) dikembalikan.
"""

import zipfile
from io import BytesIO

from engine.detector import SourcePayload, detect_kind
from extractors.csv import extract_csv
from extractors.excel import extract_excel
from extractors.pdf import extract_pdf
from extractors.json_api import extract_json
from models import ExtractionResult


_DATA_EXTENSIONS = (
    ".csv",
    ".xlsx",
    ".xls",
    ".pdf",
    ".json",
)

_SUB_EXTRACTORS = {
    "csv": extract_csv,
    "excel": extract_excel,
    "pdf": extract_pdf,
    "json": extract_json,
}


def _merge_best(results):
    best = None

    for result in results:
        if result is None:
            continue
        if result.status not in {"SUCCESS", "REVIEW"}:
            continue
        if result.value is None:
            continue
        if best is None or result.confidence > best.confidence:
            best = result

    return best


def _is_data_file(name: str) -> bool:
    return name.lower().endswith(_DATA_EXTENSIONS)


def extract_zip(payload, instruction, tahun):
    try:
        archive = zipfile.ZipFile(BytesIO(payload.content))
    except Exception as error:
        return ExtractionResult(
            status="NEED_PARSER",
            source_type="zip",
            message=f"ZIP tidak bisa dibuka: {str(error).splitlines()[0]}",
        )

    results = []

    with archive:
        for member in archive.namelist():
            if member.endswith("/"):
                continue

            if not _is_data_file(member):
                continue

            try:
                content = archive.read(member)
            except Exception:
                continue

            kind = detect_kind(
                content,
                url=member,
            )

            extractor = _SUB_EXTRACTORS.get(kind)
            if extractor is None:
                continue

            sub_payload = SourcePayload(
                url=member,
                kind=kind,
                content=content,
                content_type="",
                final_url=member,
            )

            try:
                result = extractor(
                    sub_payload,
                    instruction,
                    tahun,
                )
                results.append(result)
            except Exception as error:
                print(
                    f"    [ZIP] member '{member}' gagal: "
                    f"{str(error).splitlines()[0]}"
                )
                continue

    best = _merge_best(results)

    if best is not None:
        return best

    return ExtractionResult(
        status="NEED_PARSER",
        source_type="zip",
        message=(
            "ZIP dibuka tetapi tidak ada file data yang berhasil "
            "diekstrak."
        ),
    )
