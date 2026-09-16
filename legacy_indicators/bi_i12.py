# bi_i12.py
# BI SEKI I.12 - Total Kredit per Provinsi
# Dipisahkan dari logic main_backup agar main.py tidak terlalu panjang.

import io
import re
import time

import pandas as pd
import requests

from utils import REQUEST_HEADERS, SCRAPE_CACHE, format_id

BI_I12_XLS_URL = "https://www.bi.go.id/seki/tabel/TABEL1_12.xls"
TIMEOUT = 90

MONTHS = {
    "jan": 1, "january": 1, "januari": 1,
    "feb": 2, "february": 2, "februari": 2,
    "mar": 3, "march": 3, "maret": 3,
    "apr": 4, "april": 4,
    "may": 5, "mei": 5,
    "jun": 6, "june": 6, "juni": 6,
    "jul": 7, "july": 7, "juli": 7,
    "aug": 8, "august": 8, "agu": 8, "agustus": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12, "des": 12, "desember": 12,
}


def _norm(value):
    if value is None:
        return ""
    text = str(value).lower().replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_number(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        try:
            if pd.isna(value):
                return None
        except Exception:
            pass
        return float(value)

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "-", "--", "...", "…"}:
        return None

    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = re.sub(r"(?i)rp\.?", "", text)
    text = re.sub(r"[^0-9,.\-]", "", text)

    if not text or text == "-":
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and len(parts[1]) == 3:
            text = "".join(parts)
        elif len(parts) == 2:
            text = parts[0] + "." + parts[1]
        else:
            text = "".join(parts)
    elif "." in text:
        parts = text.split(".")
        if len(parts) > 2:
            text = "".join(parts)
        elif len(parts) == 2 and len(parts[1]) == 3:
            text = "".join(parts)

    try:
        result = float(text)
    except ValueError:
        return None

    return -result if negative else result


def _parse_year(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        try:
            year = int(float(value))
        except Exception:
            return None
        return year if 1990 <= year <= 2100 else None

    match = re.search(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)", str(value))
    return int(match.group(1)) if match else None


def _month(value):
    key = re.sub(r"[^a-z]", "", _norm(value))
    return MONTHS.get(key)


def _download_i12():
    cache_key = ("bi_i12_xls", BI_I12_XLS_URL)
    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    last_error = None

    for attempt in range(1, 4):
        try:
            print(
                f"    [BI-KREDIT] GET TABEL1_12.xls "
                f"(attempt {attempt}/3)"
            )

            response = requests.get(
                BI_I12_XLS_URL,
                headers=REQUEST_HEADERS,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
            response.raise_for_status()

            SCRAPE_CACHE[cache_key] = response.content
            return response.content

        except Exception as error:
            last_error = error
            if attempt < 3:
                time.sleep(attempt * 2)

    raise RuntimeError(
        f"TABEL1_12.xls tidak dapat diunduh: {last_error}"
    )


def _find_kalsel_row(df):
    max_cols = min(len(df.columns), 10)

    for row_index in range(len(df)):
        parts = []

        for col in range(max_cols):
            value = df.iat[row_index, col]
            try:
                if pd.isna(value):
                    continue
            except Exception:
                pass
            parts.append(_norm(value))

        row_text = " ".join(parts)

        if (
            "kalimantan selatan" in row_text
            or "south kalimantan" in row_text
        ):
            return row_index

    return None


def _year_maps(df, rows):
    cols = len(df.columns)
    maps = []

    for row in range(rows):
        current_year = None
        mapping = []

        for col in range(cols):
            detected = _parse_year(df.iat[row, col])

            if detected is not None:
                current_year = detected

            mapping.append(current_year)

        maps.append(mapping)

    return maps


def _december_columns(df, tahun):
    rows = min(50, len(df))
    cols = len(df.columns)
    maps = _year_maps(df, rows)

    candidates = []

    for month_row in range(rows):
        month_count = sum(
            1
            for col in range(cols)
            if _month(df.iat[month_row, col]) is not None
        )

        # Hindari salah menganggap sel tunggal sebagai header bulan.
        if month_count < 3:
            continue

        for col in range(cols):
            if _month(df.iat[month_row, col]) != 12:
                continue

            if any(
                maps[year_row][col] == int(tahun)
                for year_row in range(month_row + 1)
            ):
                candidates.append(col)

    result = []
    for col in candidates:
        if col not in result:
            result.append(col)

    return result


def _extract_sheet(df, sheet_name, tahun):
    if df.empty:
        return None

    row_index = _find_kalsel_row(df)
    if row_index is None:
        return None

    december_cols = _december_columns(df, tahun)
    if not december_cols:
        return None

    for col in december_cols:
        value = _parse_number(df.iat[row_index, col])

        if value is None or value <= 0:
            continue

        print(f"    [BI-KREDIT] Sheet        = {sheet_name}")
        print(f"    [BI-KREDIT] Baris Kalsel = {row_index + 1}")
        print(f"    [BI-KREDIT] Kolom Des    = {col + 1}")
        print(
            f"    [BI-KREDIT] Total Kredit = "
            f"{format_id(value)} miliar Rp"
        )

        return float(value)

    return None


def scrape_bi_i12_credit(
    url,
    tahun,
    description,
    region_hint=None,
):
    """
    Ambil Total Kredit Kalimantan Selatan posisi Desember tahun target.

    Sumber:
        BI SEKI Tabel I.12
        Posisi Pinjaman/Kredit Rupiah dan Valas Bank Umum dan BPR
        Menurut Lokasi Proyek Provinsi.

    Signature sengaja kompatibel dengan main.py:
        scrape_bi_i12_credit(url=url, tahun=tahun, description=description)
    """

    tahun = int(tahun)

    cache_key = ("bi_i12_total_credit", tahun)

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    print(
        f"    [BI-KREDIT] Mengambil Total Kredit "
        f"Kalimantan Selatan akhir tahun {tahun}..."
    )
    print("    [BI-KREDIT] Tabel        = BI SEKI I.12")
    print(f"    [BI-KREDIT] XLS          = {BI_I12_XLS_URL}")

    content = _download_i12()

    try:
        excel = pd.ExcelFile(io.BytesIO(content))
    except Exception as error:
        raise RuntimeError(
            "TABEL1_12.xls berhasil diunduh tetapi gagal dibaca. "
            f"Pastikan xlrd terpasang. Detail: {error}"
        ) from error

    print(
        "    [BI-KREDIT] Sheets       = "
        + ", ".join(str(name) for name in excel.sheet_names)
    )

    for sheet_name in excel.sheet_names:
        try:
            df = pd.read_excel(
                excel,
                sheet_name=sheet_name,
                header=None,
                dtype=object,
            )
        except Exception as error:
            print(
                f"    [BI-KREDIT] Skip {sheet_name}: {error}"
            )
            continue

        value = _extract_sheet(
            df,
            sheet_name,
            tahun,
        )

        if value is not None:
            SCRAPE_CACHE[cache_key] = float(value)
            return float(value)

    raise RuntimeError(
        "Nilai BI I.12 tidak ditemukan untuk "
        f"Kalimantan Selatan, Desember {tahun}. "
        "Tidak ada sheet dengan kombinasi tahun target + "
        "bulan Desember + baris Kalimantan Selatan."
    )


__all__ = ["scrape_bi_i12_credit"]
