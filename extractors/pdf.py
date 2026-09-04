import re
from io import BytesIO

import pandas as pd
import pdfplumber

from engine.tabular import extract_from_dataframes, normalize_region
from models import ExtractionResult


def _tables_from_pdf(content):
    frames = []

    with pdfplumber.open(BytesIO(content)) as pdf:
        for page in pdf.pages:
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []

            for table in tables:
                if not table or len(table) < 2:
                    continue
                header = [
                    str(x or "").strip()
                    for x in table[0]
                ]
                rows = table[1:]
                try:
                    frames.append(pd.DataFrame(rows, columns=header))
                except Exception:
                    try:
                        frames.append(pd.DataFrame(table))
                    except Exception:
                        pass

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

    return _line_fallback(
        payload.content,
        instruction,
        tahun,
    )
