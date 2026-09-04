"""
Adapter PREVALENSI STUNTING KALIMANTAN SELATAN (Auto project).

Perbaikan atas modul legacy `prevelensi_stunting.py`:
- URL PDF DIAMBIL DARI SPREADSHEET (kolom Sumber_1), bukan mirror hardcode.
- Sumber_1 boleh berisi beberapa link sekaligus (dipisah ';' atau baris baru).
- Semua kandidat PDF diunduh, tahun laporan masing-masing dideteksi,
  lalu PDF yang tahunnya cocok dengan tahun yang diminta dipakai.
- Jika tidak ada PDF yang memuat data tahun yang diminta (mis. 2020/2023
  karena SSGI bukan survei tahunan) -> tahun dilewati (UNAVAILABLE),
  sehingga nilai tahun X tidak tertulis ke tahun Y.
- Jika PDF memuat tahun tsb & baris Kalimantan Selatan ditemukan
  -> SUCCESS dengan total prevalensi stunting (severely + stunting).
"""

import re
import urllib.request
from collections import Counter
from io import BytesIO
from urllib.parse import parse_qs, urljoin, urlparse

import pdfplumber
import requests

from config import REQUEST_HEADERS

PROVINSI_TARGET = "Kalimantan Selatan"

PDF_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Accept": "application/pdf,*/*",
}

_PDF_CACHE = {}
_VALUE_CACHE = {}


class StuntingUnavailableError(RuntimeError):
    """Data tidak tersedia untuk tahun ini (URL kosong / PDF bukan tahun tsb)."""


def parse_indo_number(text):
    text = str(text).strip().replace(".", "").replace(",", ".")
    return float(text)


# ============================================================
# RESOLVE URL (spreadsheet) -> PDF
# ============================================================

_DRIVE_HOSTS = ("drive.google.com", "docs.google.com")


def _google_drive_id(url):
    """
    Ekstrak file ID dari link Google Drive / Google Docs.
    Hanya untuk domain drive.google.com / docs.google.com.
    ID seperti G-FZ4EGPX92D dari googletagmanager (Google Analytics)
    TIDAK boleh dianggap file drive.
    """
    parsed = urlparse(url)

    if parsed.netloc.lower() not in _DRIVE_HOSTS:
        return None

    match = re.search(r"/file/d/([^/?#]+)", url)
    if match:
        return match.group(1)

    query = parse_qs(parsed.query)
    if "id" in query:
        return query["id"][0]

    return None


def _resolve_download_url(url):
    drive_id = _google_drive_id(url)
    if drive_id:
        return (
            "https://drive.google.com/uc?export=download&id="
            + drive_id
        )
    return url


def _fetch_html(url):
    """
    GET halaman HTML. requests dulu; jika SSL gagal (antivirus memakai
    certificate store Windows, mis. AVG SSL scanning) fallback ke urllib.
    """
    try:
        response = requests.get(
            url,
            headers=REQUEST_HEADERS,
            timeout=60,
        )
        response.raise_for_status()
        return response.text
    except Exception as error:
        print(
            f"    [STUNTING] requests gagal ({error.__class__.__name__}); "
            "fallback ke urllib..."
        )

    try:
        request = urllib.request.Request(
            url,
            headers=dict(REQUEST_HEADERS),
        )

        with urllib.request.urlopen(request, timeout=60) as response:
            return response.read().decode("utf-8", "ignore")

    except Exception as error:
        print(f"    [STUNTING] Gagal membuka halaman: {error}")
        return ""


def find_pdf_links(source_url):
    """
    Cari link PDF dari URL sumber (dari spreadsheet).
    Mendukung: URL PDF langsung, halaman berisi link PDF / Google Drive,
    halaman terkait (ssgi/ski/stunting).
    """
    source_url = str(source_url or "").strip()

    if not source_url:
        return []

    if re.search(r"\.pdf(?:[?#]|$)", source_url, re.IGNORECASE):
        return [source_url]

    if _google_drive_id(source_url):
        return [_resolve_download_url(source_url)]

    response_text = _fetch_html(source_url)

    if not response_text:
        return []

    result = []
    related_pages = []

    for raw_link in re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        response_text,
        re.IGNORECASE,
    ):
        link = urljoin(source_url, raw_link)

        if (
            re.search(r"\.pdf(?:[?#]|$)", link, re.IGNORECASE)
            and link not in result
        ):
            result.append(link)
        elif _google_drive_id(link) and link not in result:
            result.append(_resolve_download_url(link))
        elif any(
            keyword in link.lower()
            for keyword in ("ssgi", "ski", "stunting")
        ):
            related_pages.append(link)

    for page_url in related_pages:
        page_text = _fetch_html(page_url)

        if not page_text:
            continue

        for raw_link in re.findall(
            r'href\s*=\s*["\']([^"\']+)["\']',
            page_text,
            re.IGNORECASE,
        ):
            link = urljoin(page_url, raw_link)

            if (
                re.search(r"\.pdf(?:[?#]|$)", link, re.IGNORECASE)
                and link not in result
            ):
                result.append(link)
            elif _google_drive_id(link) and link not in result:
                result.append(_resolve_download_url(link))

    return result


def _split_source_urls(url):
    """
    Sumber_1 boleh berisi beberapa link dipisah ';', koma, atau baris baru.
    Contoh: "https://...ssgi2019.pdf; https://...ssgi2024.pdf"
    """
    text = str(url or "")
    parts = re.split(r"[;\n]|,(?=\s*(?:https?://|www\.))", text)
    return [part.strip() for part in parts if part.strip()]


def _download_pdf(pdf_url):
    try:
        print(f"    [STUNTING] Mengunduh PDF dari: {pdf_url}")

        response = requests.get(
            pdf_url,
            headers=PDF_REQUEST_HEADERS,
            timeout=90,
        )
        response.raise_for_status()

        if (
            len(response.content) < 4
            or response.content[:4] != b"%PDF"
        ):
            raise RuntimeError(
                "File yang diunduh bukan PDF valid."
            )

        return response.content

    except Exception as error:
        # requests memakai bundle certifi. Jika antivirus (mis. AVG)
        # melakukan SSL scanning, sertifikatnya hanya ada di certificate
        # store Windows -> fallback ke urllib yang memakai store itu.
        fallback = _download_pdf_urllib(pdf_url)

        if fallback is not None:
            return fallback

        raise RuntimeError(
            f"Gagal mengunduh PDF ({pdf_url[:120]}): {error}"
        )


def _download_pdf_urllib(pdf_url):
    """Unduh PDF via urllib (certificate store Windows)."""
    try:
        request = urllib.request.Request(
            pdf_url,
            headers=dict(PDF_REQUEST_HEADERS),
        )

        with urllib.request.urlopen(request, timeout=90) as response:
            content = response.read()

        if len(content) < 4 or content[:4] != b"%PDF":
            return None

        return content

    except Exception:
        return None


def _split_source_urls(url):
    """
    Sumber_1 boleh berisi beberapa link dipisah ';', baris baru,
    atau koma sebelum link berikutnya.
    Contoh: "https://...ssgi2019.pdf; https://...ssgi2024.pdf"
    """
    text = str(url or "")
    parts = re.split(r"[;\n]|,(?=\s*(?:https?://|www\.))", text)
    return [part.strip() for part in parts if part.strip()]


def get_pdf_bytes(source_url):
    """
    Unduh SEMUA kandidat PDF dari source_url, deteksi tahun laporan
    masing-masing, lalu kembalikan mapping {report_year: pdf_content}.

    PDF yang gagal diunduh / bukan PDF valid dilewati dengan peringatan,
    supaya satu link rusak tidak mematikan link lain yang sehat.
    Jika tahun laporan sebuah PDF tidak terdeteksi, dipakai kunci None
    (tahun apa pun dianggap cocok untuk PDF ini).
    """
    cache_key = str(source_url or "").strip()

    if cache_key in _PDF_CACHE:
        return _PDF_CACHE[cache_key]

    result = {}
    index = 0

    for candidate in _split_source_urls(source_url):
        index += 1

        try:
            pdf_urls = find_pdf_links(candidate)

            if not pdf_urls:
                print(
                    f"    [STUNTING] Sumber {index} tidak memuat "
                    f"link PDF: {candidate[:100]}"
                )
                continue

        except Exception as error:
            print(
                f"    [STUNTING] Gagal membaca sumber {index} "
                f"({candidate[:100]}): {error}"
            )
            continue

        for pdf_url in pdf_urls:
            try:
                content = _download_pdf(pdf_url)
            except Exception as error:
                print(f"    [STUNTING] {error}")
                continue

            report_year = _detect_report_year(content)

            if report_year is not None:
                if report_year in result:
                    print(
                        f"    [STUNTING] PDF laporan {report_year} "
                        "sudah ada; unduhan duplikat dilewati."
                    )
                    continue

                print(
                    f"    [STUNTING] PDF terdeteksi laporan "
                    f"tahun {report_year}."
                )
            else:
                print(
                    "    [STUNTING] Tahun laporan PDF tidak "
                    "terdeteksi; dipakai sebagai fallback."
                )

            result[report_year] = content

    if not result:
        raise StuntingUnavailableError(
            "Tidak ada PDF SSGI yang berhasil diunduh dari "
            "URL sumber di spreadsheet."
        )

    _PDF_CACHE[cache_key] = result

    return result


# ============================================================
# DETEKSI TAHUN PDF
# ============================================================

def _detect_report_year(content):
    """
    Cari tahun laporan SSGI dari penanda di PDF (footer/header).
    Contoh penanda: "SURVEI STATUS GIZI INDONESIA 2024",
                    "SSGI 2024 DALAM ANGKA",
                    "STATUS GIZI INDONESIA (SSGI) 2024".
    """
    patterns = (
        r"SURVEI STATUS GIZI INDONESIA\s+(\d{4})",
        r"SSGI\s+(\d{4})\s*DALAM ANGKA",
        r"STATUS GIZI INDONESIA \(SSGI\)\s+(\d{4})",
        r"SSGI\s+(\d{4})",
        r"(\d{4})\s*DALAM ANGKA",
    )

    years = []

    with pdfplumber.open(BytesIO(content)) as pdf:
        for index, page in enumerate(pdf.pages):
            if index >= 15:
                break
            text = page.extract_text() or ""
            for pattern in patterns:
                years.extend(
                    int(value)
                    for value in re.findall(pattern, text, re.IGNORECASE)
                )

    if not years:
        return None

    return Counter(years).most_common(1)[0][0]


def _select_pdf_for_year(pdf_by_year, tahun):
    """
    Pilih PDF yang memuat data `tahun`.

    - Cocokkan tepat dulu (report_year == tahun).
    - Jika tidak ada, pakai PDF yang tahunnya tidak terdeteksi (None),
      karena tidak bisa dibuktikan salah (percaya URL spreadsheet).
    """
    tahun = int(tahun)

    if tahun in pdf_by_year:
        return pdf_by_year[tahun], "exact"

    if None in pdf_by_year:
        return pdf_by_year[None], "unknown"

    return None, None


def _available_years(pdf_by_year):
    years = sorted(
        year
        for year in pdf_by_year
        if year is not None
    )

    return years


# ============================================================
# PARSE TABEL PROVINSI
# ============================================================

def parse_province_stunting_row(page_text, province_name):
    """
    Pola baris: Aceh 6,4 5,9 - 7,0 22,2 21,2 - 23,2 71,4 70,2 - 72,5 13.378
    Total stunting = severely stunting + stunting.
    """
    escaped = re.escape(province_name)

    number = r"[\d]+,[\d]+"
    ci_range = rf"{number}\s*[-\u2013]\s*{number}"
    n_value = r"[\d.]+"

    pattern = (
        rf"{escaped}\s+"
        rf"({number})\s+{ci_range}\s+"
        rf"({number})\s+{ci_range}\s+"
        rf"({number})\s+{ci_range}\s+"
        rf"({n_value})"
    )

    match = re.search(pattern, page_text, flags=re.IGNORECASE)

    if not match:
        return None

    severely_stunting = parse_indo_number(match.group(1))
    stunting = parse_indo_number(match.group(2))
    normal = parse_indo_number(match.group(3))

    return {
        "severely_stunting": severely_stunting,
        "stunting": stunting,
        "normal": normal,
        "n": match.group(4),
        "total_stunting": round(severely_stunting + stunting, 2),
    }


def parse_province_bermasalah_gizi_row(page_text, province_name):
    """
    Format BKPK "Prevalensi Balita Bermasalah Gizi ... menurut Provinsi":
      Kalimantan Selatan 22,9 21,6 - 24,2 22,3 21,0 - 23,5 1,6 1,3 -2,0 9,1 8,3 - 10,0 4,3 3,7 - 4,9
    Kolom: Stunting, Underweight, Severe Wasting, Wasting, Overweight.
    Total stunting = angka pertama setelah nama provinsi.
    """
    escaped = re.escape(province_name)

    number = r"[\d]+,[\d]+"
    ci_range = rf"{number}\s*[-\u2013]\s*{number}"

    pattern = (
        rf"{escaped}\s+"
        rf"({number})\s+{ci_range}\s+"
        rf"({number})\s+{ci_range}\s+"
        rf"({number})\s+{ci_range}\s+"
        rf"({number})\s+{ci_range}\s+"
        rf"({number})\s+{ci_range}"
    )

    match = re.search(pattern, page_text, flags=re.IGNORECASE)

    if not match:
        return None

    return {
        "stunting": parse_indo_number(match.group(1)),
        "total_stunting": round(
            parse_indo_number(match.group(1)),
            2,
        ),
    }


_TABLE_HEADERS = (
    "prevalensi status gizi (tb/u) pada balita menurut provinsi",
    "prevalensi balita bermasalah gizi berdasarkan status gizi menurut provinsi",
)


def extract_province_stunting(pdf_bytes, province_name):
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            low = text.lower()

            if not any(
                header in low
                for header in _TABLE_HEADERS
            ):
                continue

            flat_text = re.sub(r"\s+", " ", text)

            if "prevalensi status gizi (tb/u)" in low:
                result = parse_province_stunting_row(
                    flat_text,
                    province_name,
                )
            else:
                result = parse_province_bermasalah_gizi_row(
                    flat_text,
                    province_name,
                )

            if result is not None:
                print(
                    f"    [STUNTING] Baris {province_name} "
                    f"ditemukan di halaman {page_number}."
                )
                return result

    raise RuntimeError(
        f"Baris provinsi '{province_name}' tidak ditemukan "
        "di tabel prevalensi status gizi TB/U."
    )


# ============================================================
# PUBLIC SCRAPER
# ============================================================

def scrape_stunting(
    url,
    tahun,
    description,
):
    """
    Dipanggil dari legacy_bridge / router Auto project.

    Return:
        float total prevalensi stunting Kalimantan Selatan (%).
    """
    tahun = int(tahun)
    url = str(url or "").strip()

    cache_key = ("stunting_kalsel", url, tahun)

    if cache_key in _VALUE_CACHE:
        return _VALUE_CACHE[cache_key]

    print(
        f"    [STUNTING] Mengambil prevalensi "
        f"{PROVINSI_TARGET} tahun {tahun}..."
    )

    if not url:
        raise StuntingUnavailableError(
            "URL sumber stunting kosong di spreadsheet; tahun dilewati."
        )

    pdf_by_year = get_pdf_bytes(url)

    pdf_bytes, match_kind = _select_pdf_for_year(pdf_by_year, tahun)

    if pdf_bytes is None:
        available = _available_years(pdf_by_year)
        listing = (
            ", ".join(str(year) for year in available)
            if available
            else "tidak terdeteksi"
        )

        raise StuntingUnavailableError(
            f"Tidak ada PDF yang memuat data tahun {tahun}; "
            f"tahun dilewati. Tahun tersedia: {listing}. "
            "(SSGI bukan survei tahunan: hanya 2019, 2021, 2022, 2024.)"
        )

    if match_kind == "unknown":
        print(
            f"    [STUNTING] Tahun laporan PDF tidak terdeteksi; "
            f"dipakai untuk tahun {tahun} (percaya URL spreadsheet)."
        )

    data = extract_province_stunting(
        pdf_bytes,
        PROVINSI_TARGET,
    )

    result = float(data["total_stunting"])

    if data.get("severely_stunting") is not None:
        print(
            f"    [STUNTING] Severely Stunting = "
            f"{data['severely_stunting']:.2f}%"
        )

    print(
        f"    [STUNTING] Stunting          = "
        f"{data['stunting']:.2f}%"
    )
    print(
        f"    [STUNTING] Total             = "
        f"{result:.2f}%"
    )

    _VALUE_CACHE[cache_key] = result

    return result


__all__ = [
    "scrape_stunting",
    "StuntingUnavailableError",
]
