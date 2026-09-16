import os
import re
from io import BytesIO
from urllib.parse import urljoin

import pdfplumber
import requests

from utils import REQUEST_HEADERS, SCRAPE_CACHE, format_id


# ============================================================
# IBEI - INDEKS EKONOMI BIRU INDONESIA
#
# Modul ini HANYA bertugas mengambil nilai IBEI.
# Tidak membaca Google Sheet.
# Tidak mengirim hasil ke Google Sheet.
# Tidak menyimpan Apps Script URL / token.
# ============================================================

PROVINSI_TARGET = "Kalimantan Selatan"
IBEI_REPORT_YEAR = 2024

BAPPENAS_SEARCH_URL = (
    "https://www.bappenas.go.id/datapublikasishow"
    "?q=Policy+Paper&s=indeks+ekonomi+biru&tahun=0"
)

KNOWN_PDF_URL = (
    "https://perpustakaan.bappenas.go.id/e-library/file_upload/koleksi/"
    "dokumenbappenas/konten/Dokumen%202026/"
    "Report%20IBEI%20REVISI%20(DESEMBER%202025).pdf"
)

LOCAL_PDF_FALLBACK_PATH = "Report IBEI REVISI (DESEMBER 2025).pdf"

PDF_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Accept": "application/pdf,*/*",
    "Referer": "https://www.bappenas.go.id/datapublikasishow",
}


def normalize_cell_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def parse_indonesian_number(value):
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    text = text.replace(".", "").replace(",", ".")
    text = re.sub(r"[^0-9.\-]", "", text)

    if not text:
        return None

    try:
        return float(text)
    except ValueError:
        return None


def resolve_local_pdf_path(path):
    if not path:
        return ""

    if os.path.isabs(path):
        return path

    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, path)


def create_bappenas_session():
    session = requests.Session()
    session.headers.update(REQUEST_HEADERS)

    try:
        session.get(
            "https://www.bappenas.go.id/",
            timeout=30,
        )
    except Exception:
        pass

    return session


def find_latest_ibei_pdf_url(session=None):
    print("    [IBEI] Mencari link PDF IBEI dari Bappenas...")

    requester = session if session is not None else requests

    response = requester.get(
        BAPPENAS_SEARCH_URL,
        headers=REQUEST_HEADERS,
        timeout=30,
    )
    response.raise_for_status()

    matches = re.findall(
        r'href\s*=\s*["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']',
        response.text,
        flags=re.IGNORECASE,
    )

    candidates = []

    for href in matches:
        absolute_url = urljoin(BAPPENAS_SEARCH_URL, href)

        if "ibei" in absolute_url.lower():
            candidates.append(absolute_url)

    if not candidates:
        raise RuntimeError(
            "Link PDF IBEI tidak ditemukan di halaman publikasi Bappenas."
        )

    return candidates[0]


def download_pdf_url(session, url):
    print(f"    [IBEI] Mengunduh PDF: {url}")

    response = session.get(
        url,
        headers=PDF_REQUEST_HEADERS,
        timeout=60,
        allow_redirects=True,
    )
    response.raise_for_status()

    if len(response.content) < 4 or response.content[:4] != b"%PDF":
        raise RuntimeError("Response Bappenas bukan file PDF valid.")

    return response.content


def load_local_pdf(path):
    resolved_path = resolve_local_pdf_path(path)

    print(f"    [IBEI] Membaca PDF lokal: {resolved_path}")

    if not os.path.exists(resolved_path):
        raise FileNotFoundError(
            f"PDF lokal IBEI tidak ditemukan: {resolved_path}"
        )

    with open(resolved_path, "rb") as file:
        content = file.read()

    if len(content) < 4 or content[:4] != b"%PDF":
        raise RuntimeError(
            f"File lokal bukan PDF valid: {resolved_path}"
        )

    return content


def get_ibei_pdf_bytes(source_url=""):
    source_url = str(source_url or "").strip()

    cache_key = (
        "ibei_pdf_bytes",
        source_url,
        LOCAL_PDF_FALLBACK_PATH,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    # 1. Prioritas file lokal jika tersedia.
    if LOCAL_PDF_FALLBACK_PATH:
        local_path = resolve_local_pdf_path(LOCAL_PDF_FALLBACK_PATH)

        if os.path.exists(local_path):
            content = load_local_pdf(LOCAL_PDF_FALLBACK_PATH)
            SCRAPE_CACHE[cache_key] = content
            return content

        print(
            "    [IBEI] PDF lokal tidak ada, lanjut ke sumber online."
        )

    session = create_bappenas_session()
    urls_to_try = []

    # 2. Kalau Sumber_1 Master langsung PDF, pakai itu dulu.
    if source_url and ".pdf" in source_url.lower():
        urls_to_try.append(source_url)

    # 3. URL PDF yang sudah diketahui.
    if KNOWN_PDF_URL:
        urls_to_try.append(KNOWN_PDF_URL)

    urls_to_try = list(dict.fromkeys(urls_to_try))
    last_error = None

    for pdf_url in urls_to_try:
        try:
            content = download_pdf_url(session, pdf_url)
            SCRAPE_CACHE[cache_key] = content
            return content

        except Exception as error:
            last_error = error
            print(f"    [IBEI] Gagal dari URL ini: {error}")

    # 4. Fallback pencarian publikasi Bappenas.
    try:
        fallback_url = find_latest_ibei_pdf_url(session)
        content = download_pdf_url(session, fallback_url)
        SCRAPE_CACHE[cache_key] = content
        return content

    except Exception as error:
        raise RuntimeError(
            "PDF IBEI gagal diperoleh dari file lokal maupun Bappenas. "
            f"Error terakhir: {error}. "
            f"Error URL sebelumnya: {last_error}"
        ) from error


def extract_ibei_kalsel(pdf_bytes, tahun):
    tahun = int(tahun)

    if tahun != IBEI_REPORT_YEAR:
        raise RuntimeError(
            "SOURCE_UNAVAILABLE: "
            "Laporan IBEI yang tersedia saat ini "
            f"memuat data tahun {IBEI_REPORT_YEAR}, "
            f"bukan tahun {tahun}."
        )

    target_province = normalize_cell_text(PROVINSI_TARGET)

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            tables = page.extract_tables() or []

            for table in tables:
                if not table or len(table) < 2:
                    continue

                header_row = None
                header_row_index = None

                for row_index, row in enumerate(table):
                    normalized_cells = [
                        normalize_cell_text(cell)
                        for cell in row
                    ]

                    if any(
                        target_province in cell
                        for cell in normalized_cells
                    ):
                        header_row = normalized_cells
                        header_row_index = row_index
                        break

                if header_row is None:
                    continue

                province_column = None

                for index, cell in enumerate(header_row):
                    if target_province in cell:
                        province_column = index
                        break

                if province_column is None:
                    continue

                print(
                    f"    [IBEI] Tabel halaman {page_number}; "
                    f"kolom {PROVINSI_TARGET} = {province_column}"
                )

                for row in table[header_row_index + 1:]:
                    if not row:
                        continue

                    label = normalize_cell_text(row[0])

                    if not (
                        "indeks ekonomi biru" in label
                        and "level provinsi" in label
                    ):
                        continue

                    if province_column >= len(row):
                        continue

                    raw_value = row[province_column]
                    value = parse_indonesian_number(raw_value)

                    if value is None:
                        continue

                    if not (0 <= value <= 100):
                        raise RuntimeError(
                            "Nilai IBEI di luar rentang 0-100: "
                            f"{value}"
                        )

                    print(
                        f"    [IBEI] Skor {PROVINSI_TARGET} "
                        f"{tahun} = {format_id(value)}"
                    )

                    return float(value)

    raise RuntimeError(
        "Skor IBEI Kalimantan Selatan tidak ditemukan "
        "pada tabel Lampiran 2 PDF."
    )


def scrape_ibei(url, tahun, description):
    """
    Fungsi publik yang dipanggil main.py.

    Input:
        url         = Sumber_1 dari Master_Indikator
        tahun       = tahun target
        description = Data_Diambil_1

    Return:
        float skor IBEI Kalimantan Selatan
    """

    tahun = int(tahun)

    cache_key = (
        "ibei_kalsel",
        tahun,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    print(
        f"    [IBEI] Mengambil IBEI {PROVINSI_TARGET} "
        f"tahun {tahun}..."
    )

    pdf_bytes = get_ibei_pdf_bytes(
        source_url=url
    )

    score = extract_ibei_kalsel(
        pdf_bytes,
        tahun,
    )

    SCRAPE_CACHE[cache_key] = float(score)

    return float(score)


__all__ = ["scrape_ibei"]
