import bisect
import re
from io import BytesIO

import pandas as pd
import pdfplumber

from config import OCR_DPI, OCR_ENABLED, OCR_LANG, OCR_MAX_PAGES
from engine.tabular import extract_from_dataframes
from sources.bps_utils import METRIC_SYNONYMS, parse_number_id
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


def _tables_from_pdf(content, instruction=None, tahun=None):
    # Scoring teks dilakukan di SEMUA halaman (tanpa batas) supaya halaman
    # relevan jauh di tengah dokumen (mis. lampiran tabel) tetap terpilih;
    # biaya komputasi dibatasi lewat MAX_TABLE_PAGES untuk ekstraksi tabel.
    MAX_TABLE_PAGES = 30
    TEXT_STRATEGY = {
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
    }

    frames = []

    with pdfplumber.open(BytesIO(content)) as pdf:
        pages = pdf.pages
        page_texts = []
        page_scores = []

        for page in pages:
            try:
                text = page.extract_text(layout=False) or ""
            except Exception:
                text = ""
            page_texts.append(text)

            if instruction is None or tahun is None:
                page_scores.append(0)
                continue

            normalized_text = normalize_region(text)
            has_year = str(int(tahun)) in text
            metric_hits = sum(
                normalize_region(term) in normalized_text
                for term in instruction.metric_terms
                if len(normalize_region(term)) > 2
            )
            region_hits = sum(
                re.sub(
                    r"^(prov\.|kab\.|kota)\s+",
                    "",
                    normalize_region(target),
                ) in normalized_text
                for target in instruction.target_wilayahs
            )
            page_scores.append(
                (metric_hits * 2 + region_hits) if has_year else 0
            )

        if instruction is None or tahun is None:
            candidate_indexes = set(range(min(len(pages), MAX_TABLE_PAGES)))
            has_relevant_pages = False
        else:
            ranked_indexes = sorted(
                (
                    index for index, score in enumerate(page_scores)
                    if score > 0
                ),
                key=lambda index: page_scores[index],
                reverse=True,
            )[:MAX_TABLE_PAGES]
            if not ranked_indexes:
                ranked_indexes = list(
                    range(min(len(pages), MAX_TABLE_PAGES))
                )

            has_relevant_pages = bool(ranked_indexes)
            candidate_indexes = set()
            for index in ranked_indexes:
                candidate_indexes.add(index)
                if index > 0 and len(candidate_indexes) < MAX_TABLE_PAGES:
                    candidate_indexes.add(index - 1)
                if (
                    index + 1 < len(pages)
                    and len(candidate_indexes) < MAX_TABLE_PAGES
                ):
                    candidate_indexes.add(index + 1)
                if len(candidate_indexes) >= MAX_TABLE_PAGES:
                    break

        for page_index, page in enumerate(pages):
            if page_index not in candidate_indexes:
                continue
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []

            page_frames = []
            for table in tables:
                cleaned = _clean_table(table)
                if cleaned is not None:
                    cleaned.attrs["metric_context"] = page_texts[page_index]
                    page_frames.append(cleaned)

            if not page_frames:
                nearby_relevant = (
                    page_scores[page_index] > 0
                    or (
                        not has_relevant_pages
                        and page_index < min(5, len(pages))
                    )
                    or any(
                        page_scores[index] > 0
                        for index in (page_index - 1, page_index + 1)
                        if 0 <= index < len(page_scores)
                    )
                )
                if (
                    instruction is not None
                    and tahun is not None
                    and not nearby_relevant
                ):
                    continue
                try:
                    tables = page.extract_tables(
                        table_settings=TEXT_STRATEGY,
                    ) or []
                except Exception:
                    tables = []

                for table in tables:
                    cleaned = _clean_table(table)
                    if cleaned is not None:
                        cleaned.attrs["metric_context"] = page_texts[
                            page_index
                        ]
                        page_frames.append(cleaned)

            frames.extend(page_frames)

    return frames, page_texts


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


def _metric_context_matches(text, metric_terms):
    if not metric_terms:
        return False

    normalized_text = normalize_region(text)
    matched_terms = 0
    for term in metric_terms:
        candidates = [term, *METRIC_SYNONYMS.get(term, [])]
        for candidate in candidates:
            normalized_candidate = normalize_region(candidate)
            if not normalized_candidate:
                continue
            if re.search(
                rf"(?<!\w){re.escape(normalized_candidate)}(?!\w)",
                normalized_text,
            ):
                matched_terms += 1
                break
    return matched_terms >= min(2, len(metric_terms))


def _text_table_fallback(content, instruction, tahun, page_texts=None):
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

    if page_texts is None:
        with pdfplumber.open(BytesIO(content)) as pdf:
            page_texts = []
            for page in pdf.pages:
                try:
                    page_texts.append(page.extract_text(layout=False) or "")
                except Exception:
                    page_texts.append("")

    for text in page_texts:
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]

        header_idx = None
        years = []
        for idx, line in enumerate(lines):
            if not region_marker.search(line):
                continue
            found_years = [int(y) for y in year_re.findall(line)]
            if len(found_years) >= 2:
                header_idx = idx
                years = found_years
                break

        if header_idx is None or int(tahun) not in years:
            continue

        table_context = "\n".join(
            lines[max(0, header_idx - 8):header_idx + 1]
        )
        if not _metric_context_matches(
            table_context,
            instruction.metric_terms,
        ):
            continue

        year_idx = {year: index for index, year in enumerate(years)}
        for line in lines[header_idx + 1:]:
            if re.fullmatch(r"(?:\(\d+\)\s*)+", line):
                continue

            match = row_re.match(line)
            if not match:
                continue

            name = match.group(1).strip()
            raw_values = value_re.findall(match.group(2))
            if len(raw_values) != len(years):
                continue

            parsed = [_parse_num(value) for value in raw_values]
            if any(value is None for value in parsed):
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

    missing_regions = [
        region
        for region in instruction.target_wilayahs
        if region not in region_values
    ]
    return ExtractionResult(
        status="REVIEW" if missing_regions else "SUCCESS",
        value=(
            region_values
            if instruction.wants_multi_wilayah or len(region_values) > 1
            else next(iter(region_values.values()))
        ),
        confidence=0.90 if not missing_regions else 0.65,
        source_type="pdf",
        message=(
            "Ditemukan melalui fallback tabel teks PDF."
            if not missing_regions
            else (
                "Tabel teks PDF terbaca tetapi sebagian wilayah belum "
                "ditemukan."
            )
        ),
        evidence=[
            "pdf_text_table_fallback",
            f"regions={len(region_values)}",
            f"missing_regions={len(missing_regions)}",
        ],
    )


_GENDER_CONTEXT_RE = re.compile(
    r"\bjenis kelamin\b|\blaki[- ]laki\b|\bperempuan\b"
    r"|\bmale\b|\bfemale\b|\bgender\b",
    re.IGNORECASE,
)
_WORD_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z.\-']*")
_NUM_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _gender_chart_from_words(words, instruction, tahun):
    """
    Ekstrak nilai grafik batang gender (P + L) dari daftar kata halaman.

    Pola grafik (mis. Gambar 6.1 Profil Kesehatan):
        baris kata  -> label wilayah dan dua angka (P, L) di sekitarnya
    Aturan:
      - token kata dipakai untuk mencocokkan nama wilayah (urutan baca),
        token angka dipakai sebagai nilai.
      - angka tahun (1900-2099) dibuang (termasuk "TAHUN 2024").
      - "band" nilai sebuah wilayah = baris (prev_label_end, label_end];
        band pertama: [label_end-20, label_end].
      - band dianggap sah hanya jika tepat berisi 2 angka (P dan L);
        hasil akhir = P + L.
      - jarak band antar label > 50pt dianggap bukan bagian grafik
        (mis. teks narasi di bawah grafik) dan dilewati.

    Return dict {target: value} atau None jika tidak cukup yakin.
    """
    tokens = []
    for word in words or []:
        try:
            text = str(word.get("text") or "").strip()
            top = float(word.get("top"))
        except (TypeError, ValueError):
            continue
        if not text:
            continue
        if _NUM_TOKEN_RE.fullmatch(text):
            tokens.append({"kind": "num", "text": text, "top": top})
        elif _WORD_TOKEN_RE.fullmatch(text) and len(text) > 1:
            # Kata 1 huruf dibuang: legenda grafik ("P", "L") dan
            # huruf terpisah dari tabel berformat "K O T A".
            tokens.append({"kind": "word", "text": text, "top": top})

    word_tokens = [t for t in tokens if t["kind"] == "word"]
    if not word_tokens or not instruction.target_wilayahs:
        return None

    # Stream kata (tanpa angka) agar nama wilayah yang dipisahi angka
    # pada baris yang sama tetap cocok: "KAB. HULU SUNGAI <232> UTARA".
    stream_parts = []
    stream_offsets = []
    pos = 0
    for token in word_tokens:
        if stream_parts:
            stream_parts.append(" ")
            pos += 1
        normalized = normalize_region(token["text"])
        stream_offsets.append(pos)
        stream_parts.append(normalized)
        pos += len(normalized)
    stream = "".join(stream_parts)

    matches = []
    for target in instruction.target_wilayahs:
        bare = re.sub(
            r"^(prov\.|kab\.|kota)\s+",
            "",
            normalize_region(target),
        )
        if not bare:
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(bare)}(?!\w)")
        found = pattern.search(stream)
        if found is None:
            continue
        start_index = max(
            0,
            bisect.bisect_right(stream_offsets, found.start()) - 1,
        )
        end_index = max(
            0,
            bisect.bisect_right(stream_offsets, found.end() - 1) - 1,
        )
        tops = [
            token["top"]
            for token in word_tokens[start_index:end_index + 1]
        ]
        if not tops:
            continue
        matches.append(
            {
                "target": target,
                "top_min": min(tops),
                "top_max": max(tops),
            }
        )

    if not matches:
        return None

    matches.sort(key=lambda match: (match["top_min"], match["top_max"]))

    region_values = {}
    prev_top = None
    for match in matches:
        if prev_top is None:
            low = match["top_min"] - 20
        else:
            if match["top_min"] - prev_top > 50:
                # Jarak terlalu jauh: kemungkinan teks narasi/chart lain,
                # bukan baris grafik berurutan. Band tidak dipercaya.
                continue
            low = prev_top
        high = match["top_max"]

        numbers = []
        for token in tokens:
            if token["kind"] != "num":
                continue
            if not low < token["top"] <= high:
                continue
            value = parse_number_id(token["text"])
            if value is None:
                continue
            if 1900 <= value <= 2099:
                # Tahun (mis. "TAHUN 2024") bukan nilai indikator.
                continue
            numbers.append(value)

        # Grafik gender menyediakan tepat dua angka (P dan L) per wilayah.
        if len(numbers) == 2 and all(value > 0 for value in numbers):
            region_values[match["target"]] = sum(numbers)
        prev_top = match["top_max"]

    if not region_values:
        return None
    return region_values


def _gender_chart_fallback(content, instruction, tahun, page_texts=None):
    """
    Fallback grafik batang jenis kelamin (mis. Gambar 6.1 Profil
    Kesehatan): nilai kasus per wilayah = P + L dari dua batang.

    Return ExtractionResult atau None jika tidak ada grafik yang cocok.
    """
    if not instruction.target_wilayahs:
        return None

    if page_texts is None:
        with pdfplumber.open(BytesIO(content)) as pdf:
            page_texts = []
            for page in pdf.pages:
                try:
                    page_texts.append(page.extract_text(layout=False) or "")
                except Exception:
                    page_texts.append("")

    total = len(instruction.target_wilayahs)
    best = None
    with pdfplumber.open(BytesIO(content)) as pdf:
        for page_index, page in enumerate(pdf.pages):
            if page_index >= len(page_texts):
                break
            text = page_texts[page_index]
            if not text.strip():
                continue
            if str(int(tahun)) not in text:
                continue
            if not _GENDER_CONTEXT_RE.search(text):
                continue
            if not _metric_context_matches(
                text,
                instruction.metric_terms,
            ):
                continue

            try:
                words = page.extract_words()
            except Exception:
                continue

            region_values = _gender_chart_from_words(
                words,
                instruction,
                tahun,
            )
            if not region_values:
                continue

            coverage = len(region_values) / total
            if (
                best is None
                or coverage > best["coverage"]
                or (
                    coverage == best["coverage"]
                    and len(region_values) > len(best["values"])
                )
            ):
                best = {
                    "coverage": coverage,
                    "page_index": page_index,
                    "values": region_values,
                }
            if coverage >= 1.0:
                break

    if best is None or best["coverage"] < 0.5:
        return None

    region_values = best["values"]
    if best["coverage"] >= 0.9:
        status = "SUCCESS"
        confidence = 0.85
        message = (
            "Grafik jenis kelamin terbaca penuh; nilai = jumlah P + L "
            "per wilayah."
        )
    else:
        status = "REVIEW"
        confidence = 0.65
        message = (
            "Grafik jenis kelamin terbaca sebagian; perlu review."
        )

    return ExtractionResult(
        status=status,
        value=(
            region_values
            if instruction.wants_multi_wilayah or len(region_values) > 1
            else next(iter(region_values.values()))
        ),
        confidence=confidence,
        source_type="pdf",
        message=message,
        evidence=[
            "gender_chart_sum",
            f"page={best['page_index'] + 1}",
            f"regions={len(region_values)}/{total}",
            f"gender_sum_year={int(tahun)}",
        ],
    )


def _line_fallback(content, instruction, tahun, page_texts=None):
    region_values = {}

    if page_texts is None:
        with pdfplumber.open(BytesIO(content)) as pdf:
            page_texts = []
            for page in pdf.pages:
                try:
                    page_texts.append(page.extract_text() or "")
                except Exception:
                    page_texts.append("")

    text_layer_present = any(text.strip() for text in page_texts)
    for text in page_texts:
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
                        value = float(raw.replace(",", "."))
                    except Exception:
                        continue
                    # Tahun (mis. "... TAHUN 2024") bukan nilai indikator.
                    if 1900 <= value <= 2099:
                        continue
                    values.append(value)
                if values:
                    region_values[target] = values[-1]

    if not region_values:
        return ExtractionResult(
            status="NEED_PARSER",
            source_type="pdf",
            message="PDF terbaca tetapi nilai target tidak yakin ditemukan.",
            evidence=(
                ["pdf_text_layer_present"]
                if text_layer_present
                else ["pdf_text_layer_missing"]
            ),
        )

    return ExtractionResult(
        status="REVIEW",
        value=(
            region_values
            if instruction.wants_multi_wilayah or len(region_values) > 1
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
            if instruction.wants_multi_wilayah or len(region_values) > 1
            else next(iter(region_values.values()))
        ),
        confidence=0.40,
        source_type="pdf:ocr",
        message="Ditemukan melalui OCR PDF; perlu review.",
        evidence=["pdf_ocr_fallback", f"ocr_year={int(tahun)}"],
    )


def _ocr_fallback(content, instruction, tahun, page_texts=None):
    """OCR halaman PDF scan jika parser teks biasa tidak menemukan nilai."""
    if not OCR_ENABLED:
        return None
    if page_texts and all(text.strip() for text in page_texts):
        print("    [OCR] Dilewati: PDF memiliki lapisan teks.")
        return None

    print(f"    [OCR] Mencoba OCR PDF: tahun={int(tahun)}")

    try:
        import pytesseract
    except ImportError:
        return None

    ocr_text = []
    candidate = None
    try:
        with pdfplumber.open(BytesIO(content)) as pdf:
            if page_texts is None:
                page_texts = []
                for page in pdf.pages:
                    try:
                        page_texts.append(page.extract_text() or "")
                    except Exception:
                        page_texts.append("")

            pages = pdf.pages[:len(page_texts)]
            page_indexes = [
                index
                for index, text in enumerate(page_texts[:len(pages)])
                if not text.strip()
            ][:OCR_MAX_PAGES]
            if not page_indexes:
                print("    [OCR] Dilewati: PDF memiliki lapisan teks.")
                return None

            for page_index in page_indexes:
                page = pages[page_index]
                image = page.to_image(resolution=OCR_DPI).original
                ocr_text.append(pytesseract.image_to_string(
                    image,
                    lang=OCR_LANG,
                    config="--psm 6",
                ))
                candidate = _ocr_line_fallback(
                    "\n".join(ocr_text),
                    instruction,
                    tahun,
                )
                if candidate is not None:
                    if len(instruction.target_wilayahs) == 1:
                        return candidate
                    if (
                        isinstance(candidate.value, dict)
                        and all(
                            region in candidate.value
                            for region in instruction.target_wilayahs
                        )
                    ):
                        return candidate
    except Exception as error:
        return ExtractionResult(
            status="NEED_PARSER",
            source_type="pdf:ocr",
            message=f"OCR tidak tersedia: {str(error).splitlines()[0][:200]}",
            evidence=["pdf_ocr_failed"],
        )

    print(f"    [OCR] Teks PDF terbaca: {len(''.join(ocr_text))} karakter")
    return candidate


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
    frames, page_texts = _tables_from_pdf(
        payload.content,
        instruction,
        tahun,
    )
    pdf_diagnostics = [
        f"pdf_pages_scanned={len(page_texts)}",
        f"pdf_text_pages={sum(bool(text.strip()) for text in page_texts)}",
        f"pdf_tables_extracted={len(frames)}",
        f"pdf_regions_requested={len(instruction.target_wilayahs)}",
        f"pdf_metric_terms={','.join(instruction.metric_terms[:8])}",
    ]

    result = extract_from_dataframes(
        frames,
        instruction,
        tahun,
        source_type="pdf",
    )

    if result.status == "SUCCESS":
        result.evidence.extend(pdf_diagnostics)
        return result

    text_table = _text_table_fallback(
        payload.content,
        instruction,
        tahun,
        page_texts,
    )

    if text_table is not None:
        text_table.evidence.extend(pdf_diagnostics)
        return text_table

    gender_result = _gender_chart_fallback(
        payload.content,
        instruction,
        tahun,
        page_texts,
    )

    if gender_result is not None:
        gender_result.evidence.extend(pdf_diagnostics)
        return gender_result

    line_result = _line_fallback(
        payload.content,
        instruction,
        tahun,
        page_texts,
    )

    if line_result.status in {"SUCCESS", "REVIEW"}:
        line_result.evidence.extend(pdf_diagnostics)
        return line_result

    ocr_result = _ocr_fallback(
        payload.content,
        instruction,
        tahun,
        page_texts,
    )
    if ocr_result is not None:
        ocr_result.evidence.extend(pdf_diagnostics)
        return ocr_result

    line_result.evidence.extend(pdf_diagnostics)
    return line_result
