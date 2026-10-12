import re
from io import BytesIO

import pandas as pd
import pdfplumber

from config import OCR_DPI, OCR_ENABLED, OCR_LANG, OCR_MAX_PAGES
from engine.tabular import extract_from_dataframes
from sources.region_utils import normalize_region
from models import ExtractionResult


_CORRUPTED_PATTERNS = [
    re.compile(r"\d{4}\s+(?:naik|turun)\s+\d", re.IGNORECASE),
    re.compile(r":\s*\d+[\s\S]{0,30}(?:kunjungan|penumpang|orang)", re.IGNORECASE),
    re.compile(r"m-to-m\s+\d", re.IGNORECASE),
    re.compile(r"\d+[.,]\d+%", re.IGNORECASE),
]


def _is_corrupted_column(col_name):
    text = str(col_name or "").strip()
    if not text:
        return True
    if len(text) > 100:
        return True
    nums = re.findall(r"\d+", text)
    if len(nums) > 4:
        return True
    for pattern in _CORRUPTED_PATTERNS:
        if pattern.search(text):
            return True
    return False


def _clean_table(table):
    if not table or len(table) < 2:
        return None

    header = [str(x or "").strip() for x in table[0]]

    corrupted_count = sum(1 for h in header if _is_corrupted_column(h))
    if corrupted_count > len(header) * 0.5:
        return None

    rows = table[1:]
    try:
        return pd.DataFrame(rows, columns=header)
    except Exception:
        try:
            return pd.DataFrame(table)
        except Exception:
            return None


def _tables_from_pdf(content):
    MAX_PAGES = 100
    TEXT_STRATEGY = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
    }

    frames = []

    with pdfplumber.open(BytesIO(content)) as pdf:
        pages = pdf.pages[:MAX_PAGES]

        for page in pages:
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []

            page_frames = []
            for table in tables:
                cleaned = _clean_table(table)
                if cleaned is not None:
                    page_frames.append(cleaned)

            if not page_frames:
                try:
                    tables = page.extract_tables(
                        table_settings=TEXT_STRATEGY,
                    ) or []
                except Exception:
                    tables = []

                for table in tables:
                    cleaned = _clean_table(table)
                    if cleaned is not None:
                        page_frames.append(cleaned)

            frames.extend(page_frames)

    return frames


def _parse_num(value):
    """'0,486' -> 0.486; '1.234,56' -> 1234.56; '53.74' -> 53.74."""
    text = str(value or "").strip()
    if not text:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except Exception:
        return None


def _text_table_fallback(content, instruction, tahun):
    """
    Fallback untuk PDF yang tabelnya TIDAK terdeteksi oleh
    page.extract_tables() (mis. tabel tanpa garis, seperti BRS IKG).

    Pola yang dikenali:
        Provinsi 2019 2020 2021 2022 2023 2024
        (1) (2) (3) (4) (5) (6) (7)
        Tanah Laut 0,532 0,517 0,483 0,481 0,421 0,473
        ...
        Kalimantan Selatan 0,529 0,528 0,525 0,514 0,507 0,486

    Return ExtractionResult atau None jika tidak cocok.
    """
    year_re = re.compile(r"\b(20\d{2})\b")
    region_marker = re.compile(
        r"provinsi|kabupaten/kota|kabupaten|kab/kota|wilayah|daerah",
        re.IGNORECASE,
    )
    value_re = re.compile(r"(\d+(?:[.,]\d+)?)")
    row_re = re.compile(
        r"^([A-Za-z][A-Za-z .'\-]*?)\s+((?:\d+(?:[.,]\d+)?\s*)+)$"
    )

    region_values = {}

    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            try:
                text = page.extract_text(layout=False) or ""
            except Exception:
                continue

            lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip()
            ]

            # Cari baris header tahun + marker wilayah.
            header_idx = None
            years = []

            for idx, line in enumerate(lines):
                if not region_marker.search(line):
                    continue
                found_years = [
                    int(y) for y in year_re.findall(line)
                ]
                if len(found_years) >= 2:
                    header_idx = idx
                    years = found_years
                    break

            if header_idx is None:
                continue

            if int(tahun) not in years:
                continue

            year_idx = {
                y: i for i, y in enumerate(years)
            }

            for line in lines[header_idx + 1:]:
                # skip baris kolom ref seperti "(1) (2) (3)"
                if re.fullmatch(r"(?:\(\d+\)\s*)+", line):
                    continue

                match = row_re.match(line)
                if not match:
                    continue

                name = match.group(1).strip()
                raw_values = value_re.findall(match.group(2))

                if len(raw_values) != len(years):
                    continue

                parsed = [
                    _parse_num(v)
                    for v in raw_values
                ]

                if any(v is None for v in parsed):
                    continue

                name_norm = normalize_region(name)

                for target in instruction.target_wilayahs:
                    target_norm = normalize_region(target)
                    bare = re.sub(
                        r"^(prov\.|kab\.|kota)\s+",
                        "",
                        target_norm,
                    )

                    if name_norm != target_norm and bare not in name_norm:
                        continue

                    value = parsed[year_idx[int(tahun)]]

                    if value is not None:
                        region_values[target] = value

    if not region_values:
        return None

    return ExtractionResult(
        status="SUCCESS",
        value=(
            region_values
            if len(region_values) > 1
            else next(iter(region_values.values()))
        ),
        confidence=0.75,
        source_type="pdf",
        message="Ditemukan melalui fallback tabel teks PDF.",
        evidence=["pdf_text_table_fallback"],
    )


def _line_fallback(content, instruction, tahun):
    region_values = {}

    with pdfplumber.open(BytesIO(content)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if str(tahun) not in text and len(text) > 200:
                # jangan wajibkan tahun jika dokumen memang publikasi per tahun
                pass

            for line in text.splitlines():
                line_norm = normalize_region(line)

                for target in instruction.target_wilayahs:
                    target_norm = normalize_region(target)
                    bare = re.sub(
                        r"^(prov\.|kab\.|kota)\s+",
                        "",
                        target_norm,
                    )

                    if bare not in line_norm:
                        continue

                    nums = re.findall(
                        r"(?<!\d)(-?\d+(?:[.,]\d+)?)(?!\d)",
                        line,
                    )
                    values = []
                    for raw in nums:
                        try:
                            values.append(float(raw.replace(",", ".")))
                        except Exception:
                            pass

                    if values:
                        region_values[target] = values[-1]

    if not region_values:
        return ExtractionResult(
            status="NEED_PARSER",
            source_type="pdf",
            message="PDF terbaca tetapi nilai target tidak yakin ditemukan.",
        )

    return ExtractionResult(
        status="REVIEW",
        value=(
            region_values
            if len(region_values) > 1
            else next(iter(region_values.values()))
        ),
        confidence=0.50,
        source_type="pdf",
        message="Ditemukan melalui fallback baris PDF; perlu review.",
        evidence=["pdf_line_fallback"],
    )


def _ocr_line_fallback(text, instruction, tahun):
    """Ambil kandidat angka dari baris hasil OCR; hasil selalu perlu review."""
    region_values = {}

    for line in text.splitlines():
        line_norm = normalize_region(line)

        for target in instruction.target_wilayahs:
            target_norm = normalize_region(target)
            bare = re.sub(r"^(prov\.|kab\.|kota)\s+", "", target_norm)
            if bare not in line_norm:
                continue

            numbers = re.findall(
                r"(?<!\d)(\d+(?:[.,]\d+)?)(?!\d)",
                line,
            )
            values = []
            for raw in numbers:
                value = _parse_num(raw)
                if value is not None and not 1900 <= value <= 2099:
                    values.append(value)

            if values:
                region_values[target] = values[-1]

    if not region_values:
        return None

    return ExtractionResult(
        status="REVIEW",
        value=(
            region_values
            if len(region_values) > 1
            else next(iter(region_values.values()))
        ),
        confidence=0.40,
        source_type="pdf:ocr",
        message="Ditemukan melalui OCR PDF; perlu review.",
        evidence=["pdf_ocr_fallback", f"ocr_year={int(tahun)}"],
    )


def _ocr_fallback(content, instruction, tahun):
    """OCR halaman PDF scan jika parser teks biasa tidak menemukan nilai."""
    if not OCR_ENABLED:
        return None

    print(f"    [OCR] Mencoba OCR PDF: tahun={int(tahun)}")

    try:
        import pytesseract
    except ImportError:
        return None

    ocr_text = []
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            for page in pdf.pages[:OCR_MAX_PAGES]:
                image = page.to_image(resolution=OCR_DPI).original
                ocr_text.append(
                    pytesseract.image_to_string(
                        image,
                        lang=OCR_LANG,
                        config="--psm 6",
                    )
                )
    except Exception as error:
        return ExtractionResult(
            status="NEED_PARSER",
            source_type="pdf:ocr",
            message=f"OCR tidak tersedia: {str(error).splitlines()[0][:200]}",
            evidence=["pdf_ocr_failed"],
        )

    print(f"    [OCR] Teks PDF terbaca: {len(''.join(ocr_text))} karakter")
    return _ocr_line_fallback(
        "\n".join(ocr_text),
        instruction,
        tahun,
    )


def ocr_image(content, instruction, tahun):
    """OCR satu gambar halaman viewer digital."""
    if not OCR_ENABLED:
        return None

    print(f"    [OCR] Mencoba OCR gambar: tahun={int(tahun)}")

    try:
        import pytesseract
        from PIL import Image

        image = Image.open(BytesIO(content))
        text = pytesseract.image_to_string(
            image,
            lang=OCR_LANG,
            config="--psm 6",
        )
    except Exception as error:
        return ExtractionResult(
            status="NEED_PARSER",
            source_type="image:ocr",
            message=f"OCR gambar tidak tersedia: {str(error).splitlines()[0][:200]}",
            evidence=["image_ocr_failed"],
        )

    print(f"    [OCR] Teks gambar terbaca: {len(text)} karakter")
    result = _ocr_line_fallback(text, instruction, tahun)
    if result is not None:
        result.source_type = "image:ocr"
        result.evidence.append("image_ocr_fallback")
    return result


def extract_pdf(payload, instruction, tahun):
    frames = _tables_from_pdf(payload.content)

    result = extract_from_dataframes(
        frames,
        instruction,
        tahun,
        source_type="pdf",
    )

    if result.status == "SUCCESS":
        return result

    text_table = _text_table_fallback(
        payload.content,
        instruction,
        tahun,
    )

    if text_table is not None:
        return text_table

    line_result = _line_fallback(
        payload.content,
        instruction,
        tahun,
    )

    if line_result.status in {"SUCCESS", "REVIEW"}:
        return line_result

    ocr_result = _ocr_fallback(
        payload.content,
        instruction,
        tahun,
    )
    if ocr_result is not None:
        return ocr_result

    return line_result
