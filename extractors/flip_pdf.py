"""Extractor tabel dan grafik IRBI dari viewer Flip PDF Professional."""

import ast
import json
import re
import time
from functools import lru_cache
from io import BytesIO
from urllib.parse import urljoin

import requests

from config import OCR_ENABLED, OCR_LANG, REQUEST_HEADERS
from models import ExtractionResult
from sources.region_utils import normalize_region, normalize_text


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

    search_match = re.search(
        r"searchTextJS\s*:\s*['\"]([^'\"]+)",
        config_response.text,
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

    marker = re.search(r"textForPages\s*=\s*", search_response.text)
    if marker is None:
        return []

    array_text = search_response.text[marker.end():]
    try:
        pages, _ = json.JSONDecoder().raw_decode(array_text)
    except json.JSONDecodeError:
        array_start = array_text.find("[")
        array_end = array_text.rfind("]")
        if array_start < 0 or array_end <= array_start:
            return []
        array_literal = array_text[array_start:array_end + 1]
        pages = ast.literal_eval(array_literal.replace(r"\&", "&"))

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
    raw_value = class_match.group(1) if class_match else match.group(1)
    value = _parse_number(raw_value)
    return value if 0 < value <= 250 else None


def _province_summary_value(page_text, target, tahun):
    """Ambil nilai dari tabel ringkasan IRBI 2023/2024 provinsi."""
    if "irbi 2023" not in page_text or "irbi 2024" not in page_text:
        return None

    target_pattern = (
        r"(?:prov\.\s+)*" + re.escape(_target_bare(target))
    )
    match = re.search(
        rf"(?:^|\s)\d+\s+{target_pattern}\s+"
        r"(\d+[.,]\d+)\s+(\d+[.,]\d+)",
        page_text,
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
    best_sequence = []

    for index, year in enumerate(years):
        sequence = [year]
        for candidate in years[index + 1:]:
            if candidate == sequence[-1]:
                continue
            if candidate != sequence[-1] + 1:
                break
            sequence.append(candidate)
        if len(sequence) > len(best_sequence):
            best_sequence = sequence

    return best_sequence if len(best_sequence) >= 3 else []


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


def _edition_year(pages):
    for page in pages:
        page_text = normalize_text(str(page or ""))
        match = re.search(
            r"\birbi\b.{0,120}?\btahun\s+(20\d{2})\b",
            page_text,
        )
        if match:
            return int(match.group(1))
    return None


def _annual_snapshot_value(page_text, target, tahun, is_province):
    """Ambil skor IRBI dari tabel snapshot satu tahun."""
    normalized = normalize_text(page_text)
    bare_target = re.escape(_target_bare(target))

    if is_province:
        if not re.search(
            rf"tabel\s+indeks\s+risiko\s+bencana\s+tahun\s+{int(tahun)}"
            rf"\s+tingkat\s+provinsi",
            normalized,
        ):
            return None

        match = re.search(
            rf"(?:^|\s)\d+\s+{bare_target}\s+"
            r"((?:\d+[.,]\d+\s*)+)"
            r"(?=\s+\d+\s+[a-z])",
            normalized,
        )
        if not match:
            return None

        values = re.findall(r"\d+[.,]\d+", match.group(1))
        return _parse_number(values[-1]) if values else None

    if not re.search(
        r"no\s+kabupaten/kota\s+provinsi\s+skor\s+kelas\s+risiko",
        normalized,
    ):
        return None

    match = re.search(
        rf"(?<![a-z]){bare_target}\s+(?:[a-z]+\s+){{1,5}}"
        r"(\d+[.,]\d+)\s+(?:tinggi|sedang|rendah)\b",
        normalized,
    )
    return _parse_number(match.group(1)) if match else None


@lru_cache(maxsize=32)
def _ocr_chart_values(image_url, expected_count):
    if not OCR_ENABLED or expected_count < 3:
        return None

    try:
        from PIL import Image, ImageEnhance
        import pytesseract
        from pytesseract import Output

        def image_to_data(image, config):
            try:
                return pytesseract.image_to_data(
                    image,
                    lang=OCR_LANG,
                    config=config,
                    output_type=Output.DICT,
                )
            except Exception:
                if OCR_LANG == "eng":
                    raise
                return pytesseract.image_to_data(
                    image,
                    lang="eng",
                    config=config,
                    output_type=Output.DICT,
                )

        response = _get_with_retry(image_url, timeout=30)
        image = Image.open(BytesIO(response.content)).convert("L")
        page_data = image_to_data(image, "--psm 6")
        caption_positions = [
            page_data["top"][index]
            for index, word in enumerate(page_data["text"])
            if word.strip().lower().strip(".,:") == "grafik"
            and page_data["top"][index] > image.height * 0.45
        ]
        if not caption_positions:
            return None

        caption_y = min(caption_positions)
        crop = image.crop((
            int(image.width * 0.10),
            max(0, int(caption_y - image.height * 0.35)),
            int(image.width * 0.65),
            int(caption_y - image.height * 0.07),
        ))
        crop = crop.resize((crop.width * 2, crop.height * 2))
        crop = ImageEnhance.Contrast(crop).enhance(1.7)

        candidates = []
        for page_segmentation in (6, 11):
            data = image_to_data(crop, f"--psm {page_segmentation}")
            for index, raw in enumerate(data["text"]):
                token = raw.strip().strip("\"'")
                match = re.fullmatch(r"(\d{2,3})[.,-](\d{2})", token)
                if not match:
                    continue

                try:
                    confidence = float(data["conf"][index])
                except (TypeError, ValueError):
                    continue
                if confidence < 35:
                    continue

                value = float(f"{match.group(1)}.{match.group(2)}")
                if not 0 < value <= 250:
                    continue

                left = data["left"][index]
                if left < crop.width * 0.03:
                    continue

                center = left + data["width"][index] / 2
                candidates.append((center, value, confidence))

        candidates.sort(key=lambda item: item[0])
        merge_distance = max(
            12,
            crop.width / max(expected_count, 1) * 0.30,
        )
        groups = []
        for candidate in candidates:
            if (
                groups
                and candidate[0] - groups[-1]["center"] <= merge_distance
            ):
                groups[-1]["items"].append(candidate)
                groups[-1]["center"] = sum(
                    item[0] for item in groups[-1]["items"]
                ) / len(groups[-1]["items"])
            else:
                groups.append({
                    "center": candidate[0],
                    "items": [candidate],
                })

        if len(groups) != expected_count:
            return None

        selected = [
            max(group["items"], key=lambda item: item[2])
            for group in groups
        ]
        values = [item[1] for item in selected]
        average_confidence = sum(item[2] for item in selected) / len(selected)
        confidence = min(0.86, 0.65 + average_confidence / 100 * 0.20)
        return values, confidence

    except Exception as error:
        print(
            f"    [FLIP-PDF] OCR grafik gagal: "
            f"{str(error).splitlines()[0][:200]}"
        )
        return None


def _province_chart_value(page_url, pages, target, tahun):
    target_name = _target_bare(target)
    caption_pattern = re.compile(
        r"dari\s+tahun\s+(20\d{2})\s+sampai(?:\s+dengan)?\s+"
        r"tahun\s+(20\d{2})"
    )

    for page_number, page in enumerate(pages, start=1):
        page_text = normalize_text(str(page or ""))
        if (
            "grafik nilai indeks risiko" not in page_text
            or target_name not in page_text
        ):
            continue

        years_match = caption_pattern.search(page_text)
        if not years_match:
            continue

        first_year, last_year = map(int, years_match.groups())
        if first_year > last_year or not first_year <= int(tahun) <= last_year:
            continue

        image_url = urljoin(
            page_url,
            f"../files/mobile/{page_number}.jpg",
        )
        chart = _ocr_chart_values(
            image_url,
            last_year - first_year + 1,
        )
        if chart is None:
            continue

        values, confidence = chart
        return values[int(tahun) - first_year], confidence, page_number

    return None


def extract_flip_pdf(payload, instruction, tahun):
    """Extract tabel dan grafik IRBI dari text layer viewer Flip PDF."""
    raw_html = payload.content.decode("utf-8", errors="replace")
    if "flip pdf professional" not in raw_html.lower():
        return None

    metric_terms = set(instruction.metric_terms)
    if not (
        "irbi" in metric_terms
        or {"risiko", "bencana"}.issubset(metric_terms)
    ):
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

    if target_year == _edition_year(pages):
        snapshot_values = {}
        for target in instruction.target_wilayahs:
            is_province = normalize_region(target).startswith("prov.")
            for page in pages:
                value = _annual_snapshot_value(
                    str(page or ""),
                    target,
                    target_year,
                    is_province,
                )
                if value is not None:
                    snapshot_values[target] = value
                    break

        if snapshot_values:
            print(
                f"    [FLIP-PDF] Snapshot wilayah ditemukan="
                f"{len(snapshot_values)} tahun={target_year}"
            )
            value = (
                snapshot_values
                if len(snapshot_values) > 1
                else next(iter(snapshot_values.values()))
            )
            return ExtractionResult(
                status="SUCCESS",
                value=value,
                confidence=0.86,
                source_type="flip_pdf:annual_snapshot",
                message="Ditemukan dari tabel IRBI tahunan.",
                evidence=[
                    f"year={target_year}",
                    f"regions={len(snapshot_values)}",
                    "table=annual_snapshot",
                ],
            )

    region_values = {}
    region_confidences = {}
    chart_pages = []
    for target in instruction.target_wilayahs:
        target_norm = normalize_region(target)
        is_province = target_norm.startswith("prov.")
        value = None

        if is_province:
            for page in pages:
                page_text = normalize_text(str(page or ""))
                if _target_bare(target) not in page_text:
                    continue

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
                if value is not None:
                    region_values[target] = value
                    region_confidences[target] = 0.86
                    break

            if target not in region_values:
                chart_value = _province_chart_value(
                    page_url,
                    pages,
                    target,
                    target_year,
                )
                if chart_value is not None:
                    value, confidence, page_number = chart_value
                    region_values[target] = value
                    region_confidences[target] = confidence
                    chart_pages.append(page_number)
        else:
            for page in pages:
                page_text = normalize_text(str(page or ""))
                if (
                    _target_bare(target) not in page_text
                    or "tabel nilai indeks risiko" not in page_text
                ):
                    continue

                value = _table_value(page_text, target, target_year)
                if value is not None:
                    region_values[target] = value
                    region_confidences[target] = 0.86
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
            confidence=min(region_confidences.values()),
            source_type=(
                "flip_pdf:province_chart_ocr"
                if chart_pages
                else "flip_pdf:text_layer"
            ),
            message=(
                "Ditemukan dari grafik provinsi Flip PDF."
                if chart_pages
                else "Ditemukan dari text layer viewer Flip PDF."
            ),
            evidence=[
                f"year={target_year}",
                f"regions={len(region_values)}",
                *[f"chart_page={page}" for page in chart_pages],
                "source=javascript/search_config.js",
            ],
        )

    print(
        f"    [FLIP-PDF] Tidak menemukan wilayah target="
        f"{len(instruction.target_wilayahs)} tahun={target_year}"
    )
    return None


__all__ = ["extract_flip_pdf"]
