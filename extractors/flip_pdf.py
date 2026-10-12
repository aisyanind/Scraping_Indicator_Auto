"""Extractor generik untuk viewer Flip PDF Professional."""

import ast
import json
import re
import time
from urllib.parse import urljoin

import requests

from config import REQUEST_HEADERS
from models import ExtractionResult
from sources.region_utils import normalize_region, normalize_text


YEAR_SEQUENCE = list(range(2015, 2025))


def _get_with_retry(url, timeout, attempts=3):
    last_error = None
    for attempt in range(attempts):
        try:
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=timeout,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as error:
            last_error = error
            if attempt < attempts - 1:
                time.sleep(1 + attempt)
    raise last_error


def _load_pages(page_url):
    config_url = urljoin(page_url, "javascript/config.js")
    config_response = _get_with_retry(config_url, timeout=20)

    config_text = config_response.text
    search_match = re.search(
        r"searchTextJS\s*:\s*['\"]([^'\"]+)",
        config_text,
    )
    search_path = (
        search_match.group(1)
        if search_match
        else "javascript/search_config.js"
    )

    search_response = _get_with_retry(
        urljoin(page_url, search_path),
        timeout=30,
    )

    search_text = search_response.text
    marker = re.search(r"textForPages\s*=\s*", search_text)
    if marker is None:
        return []

    array_text = search_text[marker.end():]
    decoder = json.JSONDecoder()
    try:
        pages, _ = decoder.raw_decode(array_text)
    except json.JSONDecodeError:
        array_start = array_text.find("[")
        array_end = array_text.rfind("]")
        if array_start < 0 or array_end <= array_start:
            return []
        array_literal = array_text[array_start:array_end + 1]
        array_literal = array_literal.replace(r"\&", "&")
        pages = ast.literal_eval(
            array_literal
        )

    return pages if isinstance(pages, list) else []


def _parse_number(raw):
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    else:
        raw = raw.replace(",", ".")
    return float(raw)


def _target_bare(target):
    return re.sub(
        r"^(?:(?:prov\.|kab\.|kota)\s+)+",
        "",
        normalize_region(target),
    )


def _province_value(page_text, tahun, target=None):
    labeled_match = re.search(
        rf"NILAI\s+IRB\s+{int(tahun)}.*?"
        r"(\d+[.,]\d+)\s*\((?:tinggi|sedang|rendah)\)",
        page_text,
        re.IGNORECASE,
    )
    if labeled_match:
        value = _parse_number(labeled_match.group(1))
        if 0 < value <= 250:
            return value

    if target:
        target_start = page_text.find(_target_bare(target))
        target_section = (
            page_text[target_start:target_start + 2500]
            if target_start >= 0
            else ""
        )
        if not re.search(
            rf"(?:IRBI\)?|indeks\s+risiko\s+bencana\s+indonesia"
            rf"(?:\s*\(IRBI\))?)\s+(?:tahun\s+)?{int(tahun)}",
            target_section,
            re.IGNORECASE,
        ):
            return None

        narrative_match = re.search(
            rf"{re.escape(_target_bare(target))}.*?"
            r"(?:dengan\s+nilai|nilai)\s+(\d+[.,]\d+)",
            target_section,
            re.IGNORECASE,
        )
        if narrative_match:
            value = _parse_number(narrative_match.group(1))
            if 0 < value <= 250:
                return value

    match = re.search(
        rf"NILAI\s+IRB\s+{int(tahun)}\s*:?\s*"
        r"(\d+[.,]\d+)",
        page_text,
        re.IGNORECASE,
    )
    if not match:
        return None

    section = page_text[match.end():match.end() + 1500]
    class_match = re.search(
        r"(\d+[.,]\d+)\s*\((?:tinggi|sedang|rendah)\)",
        section,
        re.IGNORECASE,
    )
    raw_value = (
        class_match.group(1)
        if class_match
        else match.group(1)
    )
    value = _parse_number(raw_value)
    return value if 0 < value <= 250 else None


def _province_summary_value(page_text, target, tahun):
    """Ambil nilai dari tabel ringkasan IRBI 2023/2024 provinsi."""
    if "irbi 2023" not in page_text or "irbi 2024" not in page_text:
        return None

    # Text layer dapat memisahkan header tabel dari baris provinsi; cari pada
    # seluruh halaman embeeded text, bukan pada jendela karakter yang sempit.
    table_text = page_text
    target_pattern = (
        r"(?:prov\.\s+)*" + re.escape(_target_bare(target))
    )
    match = re.search(
        rf"(?:^|\s)\d+\s+{target_pattern}\s+"
        r"(\d+[.,]\d+)\s+(\d+[.,]\d+)",
        table_text,
        re.IGNORECASE,
    )
    if not match:
        return None

    values = [_parse_number(match.group(1)), _parse_number(match.group(2))]
    if int(tahun) == 2023:
        return values[0]
    if int(tahun) == 2024:
        return values[1]
    return None


def _table_years(page_text):
    years = [int(raw) for raw in re.findall(r"\b(20\d{2})\b", page_text)]
    for index, year in enumerate(years):
        if year != YEAR_SEQUENCE[0]:
            continue
        sequence = []
        for candidate in years[index:]:
            if len(sequence) >= len(YEAR_SEQUENCE):
                break
            if candidate != YEAR_SEQUENCE[len(sequence)]:
                break
            sequence.append(candidate)
        if len(sequence) >= 3:
            return sequence
    return []


def _table_value(page_text, target, tahun):
    target_pattern = re.escape(_target_bare(target))
    row_pattern = re.compile(
        rf"(?:^|\s)(?:\d+\s+)?{target_pattern}\s+"
        r"((?:\d+[.,]\d+\s+)+)"
        r"(?:tinggi|sedang|rendah)",
        re.IGNORECASE,
    )
    match = row_pattern.search(page_text)
    if not match:
        return None

    values = [
        _parse_number(raw)
        for raw in re.findall(r"\d+[.,]\d+", match.group(1))
    ]
    if not values:
        return None

    years = _table_years(page_text)
    if int(tahun) not in years:
        return None

    year_index = years.index(int(tahun))
    if year_index >= len(values):
        return None

    value = values[year_index]
    return value if 0 < value <= 250 else None


def extract_flip_pdf(payload, instruction, tahun):
    """Extract nilai historis dari text layer viewer Flip PDF."""
    raw_html = payload.content.decode("utf-8", errors="replace")
    if "flip pdf professional" not in raw_html.lower():
        return None

    page_url = payload.final_url or payload.url
    try:
        pages = _load_pages(page_url)
    except Exception as error:
        print(
            f"    [FLIP-PDF] Gagal membaca text layer: "
            f"{str(error).splitlines()[0][:200]}"
        )
        return None

    print(f"    [FLIP-PDF] Text layer pages={len(pages)}")

    target_year = int(tahun)
    if target_year not in YEAR_SEQUENCE:
        print(
            f"    [FLIP-PDF] Tahun {target_year} tidak tersedia; "
            f"rentang={YEAR_SEQUENCE[0]}-{YEAR_SEQUENCE[-1]}"
        )
        return None

    region_values = {}
    for target in instruction.target_wilayahs:
        target_norm = normalize_region(target)
        is_province = target_norm.startswith("prov.")

        for page in pages:
            page_text = normalize_text(str(page or ""))
            if _target_bare(target) not in page_text:
                continue

            if is_province:
                value = _province_summary_value(
                    page_text,
                    target,
                    target_year,
                )
                if value is None:
                    value = _province_value(
                        page_text,
                        target_year,
                        target=target,
                    )
            elif "tabel nilai indeks risiko" in page_text:
                value = _table_value(page_text, target, target_year)
            else:
                value = None

            if value is not None:
                region_values[target] = value
                break

    if region_values:
        print(
            f"    [FLIP-PDF] Wilayah ditemukan="
            f"{len(region_values)} tahun={target_year}"
        )
        value = (
            region_values
            if len(region_values) > 1
            else next(iter(region_values.values()))
        )
        return ExtractionResult(
            status="SUCCESS",
            value=value,
            confidence=0.86,
            source_type="flip_pdf:text_layer",
            message="Ditemukan dari text layer Flip PDF.",
            evidence=[
                f"year={target_year}",
                f"regions={len(region_values)}",
                "source=javascript/search_config.js",
            ],
        )

    print(
        f"    [FLIP-PDF] Tidak menemukan wilayah target="
        f"{len(instruction.target_wilayahs)} tahun={target_year}"
    )
    return None


__all__ = ["extract_flip_pdf"]