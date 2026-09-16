from utils import *
import re
import pandas as pd
import pdfplumber
import requests
from io import BytesIO
import zipfile

def parse_bi_number(
    value
):

    if value is None:

        return None

    try:

        if pd.isna(
            value
        ):

            return None

    except Exception:

        pass

    if isinstance(
        value,
        (int, float)
    ):

        return float(
            value
        )

    text = (
        str(
            value
        )
        .strip()
    )

    text = re.sub(
        r"[^0-9,.\-]",
        "",
        text
    )

    if not text:

        return None

    try:

        if "," in text:

            text = text.replace(
                ",",
                ""
            )

        return float(
            text
        )

    except ValueError:

        return None

def download_bi_year_end_zip(
    tahun
):
    """
    Untuk mengambil posisi DPK Desember tahun Y,
    gunakan publikasi SEKI Januari tahun Y+1.

    Contoh:
    Desember 2025 -> SEKI Januari 2026.
    """

    tahun = int(tahun)
    publication_year = tahun + 1

    cache_key = (
        "bi_year_end_zip",
        tahun,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    page_url = (
        BI_JANUARY_PAGE_TEMPLATE
        .format(
            year=publication_year
        )
    )

    print(
        f"    [BI] Membuka SEKI Januari "
        f"{publication_year} untuk Desember {tahun}..."
    )

    page_response = None
    page_error = None
    for attempt in range(1, 4):
        try:
            page_response = requests.get(
                page_url,
                headers=REQUEST_HEADERS,
                timeout=60,
            )
            break
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as error:
            page_error = error
            print(f"    [BI] Gagal membuka halaman (percobaan {attempt}/3): {error}")
            if attempt < 3:
                time.sleep(4 * attempt)

    if page_response is None:
        raise RuntimeError(
            f"Halaman SEKI Januari {publication_year} tidak dapat diakses: "
            f"{page_error}"
        )

    page_response.raise_for_status()

    zip_url = find_zip_url_in_html(
        page_url,
        page_response.text,
    )

    candidate_urls = []

    if zip_url:
        candidate_urls.append(
            zip_url
        )

    base_documents = (
        "https://www.bi.go.id/id/"
        "statistik/ekonomi-keuangan/"
        "seki/Documents/"
    )

    candidate_urls.extend([
        urljoin(
            base_documents,
            f"SEKI_JANUARI_{publication_year}.zip",
        ),
        urljoin(
            base_documents,
            f"SEKI JANUARI {publication_year}.zip",
        ),
    ])

    seen = set()
    last_error = None

    for candidate_url in candidate_urls:

        if candidate_url in seen:
            continue

        seen.add(
            candidate_url
        )

        try:
            response = None
            for attempt in range(1, 4):
                try:
                    response = requests.get(
                        candidate_url,
                        headers=REQUEST_HEADERS,
                        timeout=120,
                    )
                    break
                except (
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                ) as error:
                    last_error = error
                    print(
                        f"    [BI] Gagal mengunduh ZIP "
                        f"(percobaan {attempt}/3): {error}"
                    )
                    if attempt < 3:
                        time.sleep(4 * attempt)

            if response is None:
                continue

            response.raise_for_status()

            if not response.content.startswith(
                b"PK"
            ):
                continue

            result = (
                response.content,
                candidate_url,
            )

            SCRAPE_CACHE[
                cache_key
            ] = result

            return result

        except Exception as error:
            last_error = error

    raise RuntimeError(
        f"ZIP SEKI Januari {publication_year} "
        f"untuk data Desember {tahun} "
        "tidak berhasil diunduh. "
        f"Error terakhir: {last_error}"
    )


# ============================================================
# BI: PILIH FILE TABEL I.19
# ============================================================

def select_bi_i19_member(
    names
):

    candidates = []

    for name in names:

        lower_name = (
            name.lower()
        )

        if lower_name.endswith(
            "/"
        ):

            continue

        if "." not in lower_name:

            continue

        extension = (
            lower_name
            .rsplit(
                ".",
                1
            )[-1]
        )

        if extension not in {
            "pdf",
            "xlsx",
            "xls",
            "csv",
        }:

            continue

        normalized = re.sub(
            r"[^a-z0-9]+",
            "",
            lower_name
        )

        score = 0

        if "tabel119" in normalized:

            score += 120

        if "tabeli19" in normalized:

            score += 120

        if "table119" in normalized:

            score += 120

        if "tablei19" in normalized:

            score += 120

        if re.search(
            r"(?:^|[^0-9])"
            r"1[._ -]?19"
            r"(?:[^0-9]|$)",
            lower_name
        ):

            score += 80

        if extension == "xls":

            score += 30

        elif extension == "xlsx":

            score += 25

        elif extension == "pdf":

            score += 20

        elif extension == "csv":

            score += 10

        if score >= 80:

            candidates.append(
                (
                    score,
                    name
                )
            )

    if not candidates:

        return None

    candidates.sort(
        key=lambda item:
            item[0],
        reverse=True,
    )

    return (
        candidates[0][1]
    )


# ============================================================
# BI: BACA EXCEL / CSV
# ============================================================

def read_bi_frames(
    file_bytes,
    extension
):

    try:

        if extension == "csv":

            return {
                "csv":
                    pd.read_csv(
                        BytesIO(
                            file_bytes
                        ),
                        header=None,
                        dtype=object,
                    )
            }

        return pd.read_excel(
            BytesIO(
                file_bytes
            ),
            sheet_name=None,
            header=None,
            dtype=object,
        )

    except ImportError as error:

        raise RuntimeError(
            "File BI membutuhkan pembaca Excel. "
            "Jalankan: "
            "pip install openpyxl xlrd"
        ) from error


# ============================================================
# BI: HELPER
# ============================================================

def is_december_header(
    value
):

    if value is None:

        return False

    try:

        if pd.isna(
            value
        ):

            return False

    except Exception:

        pass

    text = (
        str(
            value
        )
        .strip()
        .lower()
    )

    return bool(
        re.search(
            r"(^|[^a-z])"
            r"(des|desember|dec|december)"
            r"([^a-z]|$)",
            text
        )
    )


def contains_year(
    value,
    tahun
):

    if value is None:

        return False

    try:

        if pd.isna(
            value
        ):

            return False

    except Exception:

        pass

    return (
        str(
            int(
                tahun
            )
        )
        in
        str(
            value
        )
    )


def valid_bi_nominal(
    value
):

    return (
        value is not None
        and
        10000
        <= value
        <= 500000
    )

def get_sheet_year_range(sheet_name):
    """
    Contoh:
    'Th 2009-2018' -> (2009, 2018)
    '2019-2025'    -> (2019, 2025)
    """

    text = str(sheet_name or "").strip()

    match = re.search(
        r"((?:19|20)\d{2})\s*[-–—]\s*((?:19|20)\d{2})",
        text
    )

    if match:
        return (
            int(match.group(1)),
            int(match.group(2)),
        )

    years = re.findall(
        r"(?:19|20)\d{2}",
        text
    )

    if len(years) == 1:
        year = int(years[0])
        return year, year

    return None


def sheet_matches_year(
    sheet_name,
    tahun
):
    """
    Sheet yang jelas-jelas punya range tahun
    hanya boleh dipakai jika tahun target
    berada dalam range tersebut.
    """

    year_range = get_sheet_year_range(
        sheet_name
    )

    if year_range is None:
        return True

    start_year, end_year = (
        year_range
    )

    return (
        start_year
        <= int(tahun)
        <= end_year
    )
# ============================================================
# BI: EXTRACT EXCEL
#
# PENTING:
# fungsi ini menerima tahun
# ============================================================

def extract_bi_value_from_excel(
    file_bytes,
    extension,
    tahun
):
    """
    Ambil DPK Kalimantan Selatan posisi Desember tahun target.

    PERBAIKAN UTAMA:
    - Tidak lagi menganggap angka ke-12 sebagai Desember.
    - Membaca header bulan + tahun pada kolom yang sama.
    - Merged-cell tahun di Excel ditangani dengan forward-fill horizontal.
    - Prioritas sheet aktif 1.19.
    - Bila label tahun pada sheet aktif tidak terbaca tetapi label bulan
      terbaca, pilih kolom Desember paling kanan.
    - Jika tidak bisa dipastikan, FAILED daripada mengambil angka salah.
    """

    frames = read_bi_frames(
        file_bytes,
        extension
    )

    tahun = int(tahun)

    print(
        "    [BI] Sheet tersedia = "
        + ", ".join(
            str(name)
            for name in frames.keys()
        )
    )

    def clean_text(value):
        if value is None:
            return ""

        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass

        text = str(value).strip().lower()
        text = re.sub(
            r"[\u00a0\t\r\n]+",
            " ",
            text
        )
        text = re.sub(
            r"\s+",
            " ",
            text
        )
        return text

    month_patterns = {
        1: r"(?:jan|januari|january)",
        2: r"(?:feb|februari|february)",
        3: r"(?:mar|maret|march)",
        4: r"(?:apr|april)",
        5: r"(?:mei|may)",
        6: r"(?:jun|juni|june)",
        7: r"(?:jul|juli|july)",
        8: r"(?:agu|agt|agustus|aug|august)",
        9: r"(?:sep|sept|september)",
        10: r"(?:okt|oktober|oct|october)",
        11: r"(?:nov|november)",
        12: r"(?:des|desember|dec|december)",
    }

    def detect_month(value):
        text = clean_text(value)

        if not text:
            return None

        for month_number, pattern in month_patterns.items():
            if re.search(
                rf"(^|[^a-z]){pattern}([^a-z]|$)",
                text,
                flags=re.IGNORECASE,
            ):
                return month_number

        roman = {
            "i": 1,
            "ii": 2,
            "iii": 3,
            "iv": 4,
            "v": 5,
            "vi": 6,
            "vii": 7,
            "viii": 8,
            "ix": 9,
            "x": 10,
            "xi": 11,
            "xii": 12,
        }

        roman_text = re.sub(
            r"[\s.\-_/()]",
            "",
            text
        )

        if roman_text in roman:
            return roman[
                roman_text
            ]

        return None

    def extract_years(value):
        text = clean_text(value)

        if not text:
            return []

        return [
            int(match)
            for match in re.findall(
                r"(?<!\d)(?:19|20)\d{2}(?!\d)",
                text
            )
        ]

    def is_active_sheet(
        sheet_name
    ):
        name = (
            str(sheet_name)
            .strip()
            .lower()
            .replace("_", ".")
            .replace("-", ".")
        )

        return name == "1.19"

    # ========================================================
    # URUTKAN SHEET
    # 0 = sheet aktif 1.19
    # 1 = sheet historis yang mencakup tahun target
    # 2 = lainnya
    # ========================================================

    ordered_frames = []

    for sheet_name, frame in frames.items():

        priority = 2

        if is_active_sheet(
            sheet_name
        ):
            priority = 0

        else:
            year_range = (
                get_sheet_year_range(
                    sheet_name
                )
            )

            if (
                year_range is not None
                and
                year_range[0]
                <= tahun
                <= year_range[1]
            ):
                priority = 1

        ordered_frames.append(
            (
                priority,
                sheet_name,
                frame,
            )
        )

    ordered_frames.sort(
        key=lambda item:
            item[0]
    )

    exact_candidates = []
    active_december_candidates = []

    month_labels = {
        1: "Jan",
        2: "Feb",
        3: "Mar",
        4: "Apr",
        5: "Mei",
        6: "Jun",
        7: "Jul",
        8: "Agu",
        9: "Sep",
        10: "Okt",
        11: "Nov",
        12: "Des",
    }

    # ========================================================
    # LOOP SHEET
    # ========================================================

    for (
        priority,
        sheet_name,
        frame
    ) in ordered_frames:

        if frame.empty:
            continue

        year_range = (
            get_sheet_year_range(
                sheet_name
            )
        )

        if (
            not is_active_sheet(
                sheet_name
            )
            and
            year_range is not None
            and
            not (
                year_range[0]
                <= tahun
                <= year_range[1]
            )
        ):
            print(
                f"    [BI] Skip sheet     = "
                f"{sheet_name}"
            )
            continue

        print(
            f"    [BI] Cek sheet      = "
            f"{sheet_name}"
        )

        row_count = (
            frame.shape[0]
        )

        # ====================================================
        # CARI BARIS KALIMANTAN SELATAN
        # ====================================================

        province_rows = []

        for row_index in range(
            row_count
        ):

            row_values = (
                frame
                .iloc[
                    row_index
                ]
                .tolist()
            )

            row_text = " ".join(
                clean_text(value)
                for value in row_values
                if clean_text(value)
            )

            if (
                "south kalimantan"
                in row_text
                or
                "kalimantan selatan"
                in row_text
            ):
                province_rows.append(
                    row_index
                )

        for province_row in (
            province_rows
        ):

            print(
                f"    [BI] Baris Kalsel   = "
                f"{province_row + 1}"
            )

            row_values = (
                frame
                .iloc[
                    province_row
                ]
                .tolist()
            )

            numeric_cells = []

            for (
                column_index,
                raw_value
            ) in enumerate(
                row_values
            ):

                value = (
                    parse_bi_number(
                        raw_value
                    )
                )

                if valid_bi_nominal(
                    value
                ):
                    numeric_cells.append(
                        {
                            "column":
                                column_index,

                            "value":
                                float(value),
                        }
                    )

            print(
                "    [BI] Angka Kalsel   = "
                + (
                    ", ".join(
                        format_id(
                            item["value"],
                            0
                        )
                        for item
                        in numeric_cells
                    )
                    if numeric_cells
                    else
                    "tidak ada"
                )
            )

            # =================================================
            # HEADER
            # =================================================

            header_start = max(
                0,
                province_row - 60
            )

            header_raw = (
                frame
                .iloc[
                    header_start:
                    province_row,
                    :
                ]
                .copy()
            )

            # Excel merged-cell biasanya hanya punya nilai
            # pada sel kiri. Isi kosong diteruskan horizontal.
            header_fill_source = (
                header_raw
                .copy()
            )

            for column in (
                header_fill_source.columns
            ):

                header_fill_source[
                    column
                ] = (
                    header_fill_source[
                        column
                    ]
                    .apply(
                        lambda value:
                        pd.NA
                        if clean_text(
                            value
                        ) == ""
                        else value
                    )
                )

            header_filled = (
                header_fill_source
                .ffill(
                    axis=1
                )
            )

            mapped_columns = []

            # =================================================
            # PETAKAN TIAP KOLOM NOMINAL
            # =================================================

            for item in (
                numeric_cells
            ):

                column_index = (
                    item["column"]
                )

                if (
                    column_index
                    >= header_raw.shape[1]
                ):
                    continue

                raw_headers = (
                    header_raw
                    .iloc[
                        :,
                        column_index
                    ]
                    .tolist()
                )

                filled_headers = (
                    header_filled
                    .iloc[
                        :,
                        column_index
                    ]
                    .tolist()
                )

                detected_month = None

                # Cari bulan dari bawah ke atas.
                # Header bulan yang paling dekat ke data diprioritaskan.
                for header_value in reversed(
                    raw_headers
                ):
                    detected_month = (
                        detect_month(
                            header_value
                        )
                    )

                    if (
                        detected_month
                        is not None
                    ):
                        break

                # Kalau bulan berada dalam merged cell,
                # coba hasil horizontal fill.
                if detected_month is None:

                    for header_value in reversed(
                        filled_headers
                    ):
                        detected_month = (
                            detect_month(
                                header_value
                            )
                        )

                        if (
                            detected_month
                            is not None
                        ):
                            break

                detected_years = set()

                for header_value in (
                    raw_headers
                    +
                    filled_headers
                ):
                    detected_years.update(
                        extract_years(
                            header_value
                        )
                    )

                mapped = {
                    "sheet":
                        sheet_name,

                    "active_sheet":
                        is_active_sheet(
                            sheet_name
                        ),

                    "column":
                        column_index,

                    "value":
                        item["value"],

                    "month":
                        detected_month,

                    "years":
                        sorted(
                            detected_years
                        ),

                    "year_match":
                        tahun
                        in
                        detected_years,
                }

                mapped_columns.append(
                    mapped
                )

            # =================================================
            # CETAK MAPPING UNTUK DEBUG
            # =================================================

            if mapped_columns:

                preview = []

                for item in (
                    mapped_columns
                ):

                    month_text = (
                        month_labels.get(
                            item["month"],
                            "?"
                        )
                    )

                    year_text = (
                        "/".join(
                            str(year)
                            for year in
                            item["years"]
                        )
                        if item["years"]
                        else
                        "?"
                    )

                    preview.append(
                        f"C{item['column'] + 1}:"
                        f"{month_text}:"
                        f"{year_text}="
                        f"{format_id(item['value'], 0)}"
                    )

                print(
                    "    [BI] Mapping header = "
                    + " | ".join(
                        preview
                    )
                )

            # =================================================
            # KUMPULKAN EXACT MATCH DAN DESEMBER AKTIF
            # =================================================

            for item in (
                mapped_columns
            ):

                if (
                    item["month"] == 12
                    and
                    item["year_match"]
                ):
                    exact_candidates.append(
                        item
                    )

                if (
                    item["month"] == 12
                    and
                    item["active_sheet"]
                ):
                    active_december_candidates.append(
                        item
                    )

    # ========================================================
    # PILIH EXACT MATCH
    # ========================================================

    if exact_candidates:

        exact_candidates.sort(
            key=lambda item: (
                1
                if item[
                    "active_sheet"
                ]
                else
                0,

                item["column"],
            ),
            reverse=True,
        )

        selected = (
            exact_candidates[0]
        )

        print(
            "    [BI] Metode         = "
            "header Desember + tahun exact"
        )

        print(
            f"    [BI] Sheet terpilih = "
            f"{selected['sheet']}"
        )

        print(
            f"    [BI] Kolom terpilih = "
            f"{selected['column'] + 1}"
        )

        print(
            f"    [BI] Desember {tahun} = "
            f"{format_id(selected['value'])}"
        )

        return float(
            selected["value"]
        )

    # ========================================================
    # FALLBACK AMAN
    #
    # Pada SEKI Januari Y+1, Desember tahun Y adalah Desember
    # paling kanan pada sheet aktif. Ini hanya dipakai kalau
    # label bulan terbaca tetapi label tahun merged-cell gagal.
    # ========================================================

    if active_december_candidates:

        active_december_candidates.sort(
            key=lambda item:
                item["column"],
            reverse=True,
        )

        selected = (
            active_december_candidates[0]
        )

        print(
            "    [BI] Metode         = "
            "Desember paling kanan sheet 1.19"
        )

        print(
            f"    [BI] Sheet terpilih = "
            f"{selected['sheet']}"
        )

        print(
            f"    [BI] Kolom terpilih = "
            f"{selected['column'] + 1}"
        )

        print(
            f"    [BI] Desember {tahun} = "
            f"{format_id(selected['value'])}"
        )

        return float(
            selected["value"]
        )

    # ========================================================
    # TIDAK ADA FALLBACK POSISI ANGKA
    # ========================================================

    raise RuntimeError(
        f"DPK Kalimantan Selatan "
        f"Desember {tahun} tidak berhasil "
        "dipetakan dari header bulan + tahun. "
        "Parser dihentikan agar tidak mengambil "
        "kolom yang salah."
    )


# ============================================================
# BI: EXTRACT PDF
# ============================================================

def extract_bi_value_from_pdf(
    file_bytes,
    tahun
):

    pages = extract_pdf_pages(
        file_bytes
    )

    candidate_lines = []

    for page_text in pages:

        for line in (
            page_text
            .splitlines()
        ):

            lower = (
                line.lower()
            )

            if (
                "south kalimantan"
                in lower
                or
                "kalimantan selatan"
                in lower
            ):

                candidate_lines.append(
                    line
                )

    if not candidate_lines:

        raise RuntimeError(
            "Baris Kalimantan Selatan "
            "tidak ditemukan pada "
            "tabel BI I.19 PDF."
        )

    parsed_candidates = []

    for line in candidate_lines:

        raw_values = re.findall(
            r"\d{1,3}"
            r"(?:,\d{3})+"
            r"(?:\.\d+)?",
            line
        )

        values = [
            parse_bi_number(
                value
            )
            for value
            in raw_values
        ]

        values = [
            value
            for value
            in values
            if valid_bi_nominal(
                value
            )
        ]

        if len(
            values
        ) == 9:

            parsed_candidates.append(
                values
            )

    if len(
        parsed_candidates
    ) == 1:

        values = (
            parsed_candidates[0]
        )

        print(
            "    [BI] Kandidat PDF   = "
            +
            ", ".join(
                format_id(
                    value,
                    0
                )
                for value
                in values
            )
        )

        return float(
            values[2]
        )

    raise RuntimeError(
        f"DPK Desember {tahun} "
        "tidak dapat dipastikan "
        "dari PDF BI I.19."
    )


# ============================================================
# BI: SCRAPER I.19
# ============================================================

def scrape_bi_i19(
    url,
    tahun,
    description
):

    tahun = int(
        tahun
    )

    cache_key = (
        "bi_i19",
        tahun,
    )

    if cache_key in SCRAPE_CACHE:

        return (
            SCRAPE_CACHE[
                cache_key
            ]
        )

    print(
        f"    [BI] Mengambil DPK "
        f"akhir tahun {tahun}..."
    )

    # ========================================================
    # PENTING:
    # DPK Desember tahun Y diambil dari
    # publikasi SEKI Januari tahun Y+1.
    # ========================================================

    zip_bytes, _ = (
        download_bi_year_end_zip(
            tahun
        )
    )

    with zipfile.ZipFile(
        BytesIO(
            zip_bytes
        )
    ) as archive:

        names = (
            archive.namelist()
        )

        member = (
            select_bi_i19_member(
                names
            )
        )

        if not member:

            raise RuntimeError(
                "Tabel BI I.19 "
                "tidak ditemukan "
                "di ZIP SEKI."
            )

        file_bytes = (
            archive.read(
                member
            )
        )

    extension = (
        member
        .lower()
        .rsplit(
            ".",
            1
        )[-1]
    )

    print(
        f"    [BI] File           = "
        f"{member}"
    )

    if extension in {
        "xls",
        "xlsx",
        "csv",
    }:

        dpk = (
            extract_bi_value_from_excel(
                file_bytes,
                extension,
                tahun,
            )
        )

    elif extension == "pdf":

        dpk = (
            extract_bi_value_from_pdf(
                file_bytes,
                tahun,
            )
        )

    else:

        raise RuntimeError(
            f"Format tabel BI "
            f"tidak didukung: "
            f"{member}"
        )

    if not valid_bi_nominal(
        dpk
    ):

        raise RuntimeError(
            f"Nilai DPK {tahun} "
            f"tidak valid: {dpk}"
        )

    print(
        f"    [BI] DPK {tahun} = "
        f"{format_id(dpk)} "
        "miliar Rp"
    )

    SCRAPE_CACHE[
        cache_key
    ] = dpk

    return dpk
