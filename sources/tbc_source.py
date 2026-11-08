"""
Adapter CAKUPAN PENEMUAN KASUS TUBERKULOSIS (Auto project).

Port dari modul legacy `tbc.py`:
- Indikator 3.a tetap CAKUPAN PENEMUAN KASUS TB (%), sesuai Master
  Indikator pada Google Sheets.
- Sumber_1 CSV diaktifkan kembali untuk membaca nilai cakupan multi-tahun.
- Grafik jumlah kasus pada PDF tidak boleh dipakai sebagai nilai indikator
  persentase. PDF hanya dipakai jika tersedia ekstraksi cakupan yang valid.
- Return berupa dict {wilayah: nilai} bila multi-wilayah, atau float
  bila satu wilayah, sesuai konvensi run_indicator.
"""

import csv
import io
import re
import urllib.request
from urllib.parse import urljoin
from threading import RLock

import pdfplumber
import requests

from config import REQUEST_HEADERS
from sources.bps_utils import parse_number_id

PROVINSI_TARGET = "Prov. Kalimantan Selatan"

CSV_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Accept": "text/csv,*/*",
}

_PDF_CACHE = {}
_CSV_CACHE = {}
_ALL_DATA_CACHE = {}

# Single-flight cache: mencegah thread paralel mengunduh dan mem-parse
# sumber TBC yang sama secara bersamaan.
_TBC_CACHE_LOCK = RLock()


def _download_with_fallback(url, headers, timeout):
    """
    requests dulu; jika SSL gagal (antivirus memakai certificate store
    Windows, mis. AVG SSL scanning) fallback ke urllib.
    """
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        return response.content

    except Exception as error:
        print(
            f"    [TBC] requests gagal ({error.__class__.__name__}); "
            "fallback ke urllib..."
        )

    request = urllib.request.Request(
        url,
        headers=dict(headers),
    )

    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


# ============================================================
# SUMBER 1: OPEN DATA KALSEL CSV
# ============================================================

def resolve_opendata_csv_url(dataset_page_url):
    match = re.search(
        r"[0-9a-fA-F]{8}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{12}",
        str(dataset_page_url or ""),
    )

    if not match:
        raise RuntimeError(
            "Tidak bisa menemukan dataset ID dari URL Sumber_1: "
            f"{dataset_page_url}"
        )

    dataset_id = match.group(0)

    return (
        "https://opendata.kalselprov.go.id/api/v1/apps/"
        "dataset/download/"
        f"{dataset_id}?response_type=csv"
    )


def parse_sumber1_csv(csv_text):
    """
    Return:
        {
            tahun: {
                nama_wilayah: nilai,
                ...
            }
        }
    """

    reader = csv.reader(
        io.StringIO(csv_text),
        delimiter=";",
    )

    rows = list(reader)

    if not rows:
        return {}

    header = [
        str(item).strip()
        for item in rows[0]
    ]

    hasil = {}

    for row in rows[1:]:
        if not row:
            continue

        tahun_text = str(row[0]).strip()

        if not re.fullmatch(
            r"\d{4}",
            tahun_text,
        ):
            continue

        tahun = int(tahun_text)

        per_wilayah = {}

        for idx in range(
            1,
            len(header),
        ):
            if idx >= len(row):
                continue

            nilai = parse_number_id(
                row[idx]
            )

            if nilai is not None:
                per_wilayah[
                    header[idx]
                ] = nilai

        hasil[tahun] = per_wilayah

    return hasil


# ============================================================
# SUMBER 2: PDF PROFIL KESEHATAN
# ============================================================

# Cache khusus hasil ekstraksi cakupan persentase PDF.
_PDF_COVERAGE_CACHE = {}


def _extract_tbc_coverage_from_pdf(pdf_content):
    """
    Membaca nilai CAKUPAN PENEMUAN KASUS TUBERKULOSIS (%)
    dari Tabel 56 Profil Kesehatan.

    Tidak mengambil:
    - Jumlah kasus TB (9.528)
    - Cakupan penemuan kasus TB anak (132,9%)
    - Persentase pelayanan terduga TB (86,1%)

    Return:
        (tahun, {wilayah: nilai_persen})
    """

    pattern = re.compile(
        r"CAKUPAN\s+PENEMUAN\s+KASUS\s+"
        r"TUBERKULOSIS\s*\(\s*%\s*\)\s*"
        r"([0-9]+(?:[.,][0-9]+)?)",
        re.IGNORECASE,
    )

    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""

            upper = page_text.upper()

            # Pastikan sumber adalah Tabel 56.
            if not re.search(r"\bTABEL\s+56\b", upper):
                continue

            # Pastikan tabel memang tahun yang dicari.
            year_match = re.search(
                r"\bTAHUN\s+(20\d{2})\b",
                upper,
            )

            if not year_match:
                continue

            match = pattern.search(
                re.sub(r"\s+", " ", page_text)
            )

            if not match:
                continue

            tahun = int(year_match.group(1))

            nilai = parse_number_id(match.group(1))

            if nilai is None:
                continue

            # Hanya masukkan nilai tingkat provinsi.
            return tahun, {
                PROVINSI_TARGET: float(nilai)
            }

    return None, {}


def get_sumber2_coverage_data(gdrive_share_url):
    """
    Mengambil persentase cakupan penemuan kasus TB
    dari Tabel 56 PDF Profil Kesehatan.

    Return:
        (tahun, {Prov. Kalimantan Selatan: nilai})
    """

    direct_url = resolve_gdrive_direct_url(gdrive_share_url)

    cache_key = direct_url

    with _TBC_CACHE_LOCK:

        if cache_key in _PDF_COVERAGE_CACHE:
            return _PDF_COVERAGE_CACHE[cache_key]

        print(
            "    [TBC] [SUMBER PDF] "
            "Mengunduh PDF untuk ekstraksi cakupan TB..."
        )

        content = _download_with_fallback(
            direct_url,
            REQUEST_HEADERS,
            180,
        )

        if not content.startswith(b"%PDF"):
            raise RuntimeError(
                "File sumber bukan PDF yang valid."
            )

        result = _extract_tbc_coverage_from_pdf(content)

        tahun, values = result

        if values:
            print(
                f"    [TBC] [SUMBER PDF] "
                f"Cakupan TB Provinsi tahun {tahun} = "
                f"{next(iter(values.values()))}%"
            )
        else:
            print(
                "    [TBC] [SUMBER PDF] "
                "Nilai cakupan TB tidak ditemukan."
            )

        _PDF_COVERAGE_CACHE[cache_key] = result

        return result

def resolve_gdrive_direct_url(share_url):
    match = re.search(
        r"/d/([a-zA-Z0-9_-]+)",
        str(share_url or ""),
    )

    if not match:
        match = re.search(
            r"[?&]id=([a-zA-Z0-9_-]+)",
            str(share_url or ""),
        )

    if not match:
        raise RuntimeError(
            "Tidak bisa menemukan file ID dari URL "
            f"Google Drive Sumber_2: {share_url}"
        )

    file_id = match.group(1)

    return (
        "https://drive.google.com/uc?"
        f"export=download&id={file_id}"
    )



def _parse_tbc_case_counts_words(words, page_text):
    """
    Parse Grafik 6.1 dari koordinat label angka PDF.
    Pada grafik, angka laki-laki/perempuan dicetak sebagai pasangan
    horizontal dan label wilayah tersusun dari atas ke bawah.
    """
    upper = str(page_text or "").upper()
    if not (
        "JUMLAH SEMUA KASUS TUBERKULOSIS" in upper
        or "JUMLAH SELURUH KASUS TB" in upper
    ):
        return None, {}

    year_match = re.search(r"\bTAHUN\s+(20\d{2})\b", upper)
    if not year_match:
        return None, {}
    tahun = int(year_match.group(1))

    ordered_regions = [
        "Kota Banjarbaru",
        "Kota Banjarmasin",
        "Kab. Balangan",
        "Kab. Tanah Bumbu",
        "Kab. Tabalong",
        "Kab. Hulu Sungai Utara",
        "Kab. Hulu Sungai Tengah",
        "Kab. Hulu Sungai Selatan",
        "Kab. Tapin",
        "Kab. Barito Kuala",
        "Kab. Banjar",
        "Kab. Kotabaru",
        "Kab. Tanah Laut",
    ]

    # Pastikan semua label wilayah ada pada teks halaman agar parser tidak
    # salah menganggap grafik lain sebagai Grafik 6.1.
    compact = re.sub(r"\s+", " ", upper)
    label_patterns = [
        r"KOTA\s+BANJARBARU",
        r"KOTA\s+BANJARMASIN",
        r"KAB\.?\s+BALANGAN",
        r"KAB\.?\s+TANAH\s+BUMBU",
        r"KAB\.?\s+TABALONG",
        r"KAB\.?\s+HULU\s+SUNGAI\s+UTARA",
        r"KAB\.?\s+HULU\s+SUNGAI\s+TENGAH",
        r"KAB\.?\s+HULU\s+SUNGAI\s+SELATAN",
        r"KAB\.?\s+TAPIN",
        r"KAB\.?\s+BARITO\s+KUALA",
        r"KAB\.?\s+BANJAR",
        r"KAB\.?\s+KOTABARU",
        r"KAB\.?\s+TANAH\s+LAUT",
    ]
    # Beberapa label Hulu Sungai terpotong oleh label angka pada urutan
    # teks PDF; validasi token wilayah secara terpisah.
    if not all(
        token in compact
        for token in (
            "KOTA BANJARBARU", "KOTA BANJARMASIN", "BALANGAN",
            "TANAH BUMBU", "TABALONG", "HULU SUNGAI", "TAPIN",
            "BARITO KUALA", "KAB. BANJAR", "KOTABARU", "TANAH LAUT",
        )
    ):
        return tahun, {}

    numeric_words = []
    for word in words:
        x0 = float(word.get("x0", 0))
        top = float(word.get("top", 9999))
        token = str(word.get("text", "")).strip()
        if not (180 < x0 < 520 and top < 320):
            continue
        if not re.fullmatch(r"\d[\d.,]*", token) or token == "2024":
            continue
        # Angka grafik berupa bilangan bulat; titik adalah pemisah ribuan.
        number = int(re.sub(r"\D", "", token))
        numeric_words.append((top, x0, number))

    numeric_words.sort(key=lambda item: (item[0], item[1]))

    # Kelompokkan label angka yang sejajar secara horizontal. Pada halaman
    # sumber, dua gender dalam satu wilayah terpisah maksimal 8 pt secara y.
    pairs = []
    used = set()
    for i, first in enumerate(numeric_words):
        if i in used:
            continue
        candidates = [
            (j, second)
            for j, second in enumerate(numeric_words)
            if j != i and j not in used
            and abs(second[0] - first[0]) <= 8
        ]
        if not candidates:
            continue
        j, second = min(
            candidates,
            key=lambda item: (
                abs(item[1][0] - first[0]),
                abs(item[1][1] - first[1]),
            ),
        )
        used.add(i)
        used.add(j)
        pairs.append((
            (first[0] + second[0]) / 2,
            first[2] + second[2],
        ))

    pairs.sort(key=lambda item: item[0])
    if len(pairs) != 13:
        return tahun, {}

    values = {
        wilayah: int(pair[1])
        for wilayah, pair in zip(ordered_regions, pairs)
    }
    province_total = sum(values.values())

    narrative_match = re.search(
        r"(?:mencapai\s+sebesar|reaches)\s+([\d.,]+)",
        compact,
        flags=re.IGNORECASE,
    )
    if narrative_match:
        narrative_total = int(re.sub(r"\D", "", narrative_match.group(1)))
        if narrative_total != province_total:
            raise ValueError(
                "Total grafik TB tidak konsisten: "
                f"jumlah kab/kota={province_total}, "
                f"narasi provinsi={narrative_total}."
            )

    values[PROVINSI_TARGET] = province_total
    return tahun, values


def _extract_tbc_case_counts_from_pdf(pdf_content):
    """Ekstrak Grafik 6.1 dan mengembalikan (tahun, {wilayah: jumlah})."""
    with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if not ("JUMLAH SEMUA KASUS TUBERKULOSIS" in text.upper() or "JUMLAH SELURUH KASUS TB" in text.upper()):
                continue
            tahun, values = _parse_tbc_case_counts_words(
                page.extract_words(),
                text,
            )
            if values:
                return tahun, values
    return None, {}


def get_sumber2_data(gdrive_share_url):
    """
    Ekstrak jumlah kasus TB dari Grafik 6.1 PDF Profil Kesehatan.

    Return:
        (tahun, {wilayah: jumlah_kasus})

    Mode sementara indikator 3.a: gunakan jumlah kasus, bukan persentase.
    """
    direct_url = resolve_gdrive_direct_url(gdrive_share_url)

    with _TBC_CACHE_LOCK:
        if direct_url in _PDF_CACHE:
            return _PDF_CACHE[direct_url]

        print(
            f"    [TBC] [SUMBER 2] Mengunduh PDF: {direct_url}"
        )
        content = _download_with_fallback(
            direct_url,
            REQUEST_HEADERS,
            180,
        )

        if len(content) < 4 or content[:4] != b"%PDF":
            raise RuntimeError(
                "File Sumber_2 yang diterima bukan PDF valid."
            )

        result = _extract_tbc_case_counts_from_pdf(content)

        tahun, values = result
        if values:
            print(
                "    [TBC] [SUMBER 2] Jumlah kasus TB "
                f"tahun {tahun}: {len(values)} wilayah; "
                f"provinsi={values.get(PROVINSI_TARGET)}"
            )
        else:
            print(
                "    [TBC] [SUMBER 2] Grafik jumlah kasus TB "
                "tidak ditemukan atau tidak dapat dibaca."
            )

        _PDF_CACHE[direct_url] = result
        return result


# ============================================================
# AGREGASI MULTI-WILAYAH
# ============================================================

def _fetch_page_text(url):
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
            f"    [TBC] requests gagal ({error.__class__.__name__}); "
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
        print(f"    [TBC] Gagal membuka halaman: {error}")
        return ""



def _build_all_data(source_urls):
    """
    Menggabungkan data dari:
    - Open Data Kalsel CSV: data persentase multi-tahun.
    - PDF Profil Kesehatan: cakupan TB tingkat provinsi.

    Prioritas:
    CSV digunakan jika tersedia untuk tahun dan wilayah yang sama.
    PDF mengisi tahun yang belum tersedia di CSV.

    Tidak mencampurkan jumlah kasus dengan persentase.
    """

    all_data = {}

    for index, source_url in enumerate(source_urls, start=1):

        source_url = str(source_url or "").strip()

        if not source_url:
            continue

        try:

            # ==========================================
            # SUMBER CSV OPEN DATA KALSEL
            # ==========================================

            if "opendata.kalselprov.go.id" in source_url.lower():

                per_tahun = get_sumber1_data(source_url)

                for tahun, per_wilayah in per_tahun.items():

                    for nama_wilayah, nilai in per_wilayah.items():

                        all_data.setdefault(
                            nama_wilayah,
                            {}
                        ).setdefault(
                            int(tahun),
                            nilai
                        )

                print(
                    f"    [TBC] [SUMBER {index}] "
                    "CSV berhasil digabungkan."
                )

            # ==========================================
            # SUMBER PDF PROFIL KESEHATAN
            # ==========================================

            else:

                tahun, values = get_sumber2_coverage_data(
                    source_url
                )

                if not values or tahun is None:
                    print(
                        f"    [TBC] [SUMBER {index}] "
                        "PDF tidak menyediakan nilai cakupan TB."
                    )
                    continue

                for nama_wilayah, nilai in values.items():

                    # CSV tetap menjadi prioritas jika tahun sama.
                    all_data.setdefault(
                        nama_wilayah,
                        {}
                    ).setdefault(
                        int(tahun),
                        nilai
                    )

                print(
                    f"    [TBC] [SUMBER {index}] "
                    f"PDF berhasil digabungkan untuk tahun {tahun}."
                )

        except Exception as error:

            print(
                f"    [TBC] [SUMBER {index}] gagal: "
                f"{str(error).splitlines()[0][:200]}"
            )

    return all_data



def get_sumber1_data(dataset_page_url):
    cache_key = str(dataset_page_url or "").strip()

    with _TBC_CACHE_LOCK:
        if cache_key in _CSV_CACHE:
            return _CSV_CACHE[cache_key]

        csv_url = resolve_opendata_csv_url(cache_key)
        print(
            f"    [TBC] [SUMBER 1] Mengunduh CSV: {csv_url}"
        )

        content = _download_with_fallback(
            csv_url,
            CSV_REQUEST_HEADERS,
            60,
        )

        data = parse_sumber1_csv(
            content.decode("utf-8", "ignore")
        )

        print(
            "    [TBC] [SUMBER 1] Tahun tersedia: "
            f"{sorted(data.keys())}"
        )

        _CSV_CACHE[cache_key] = data
        return data


def scrape_tbc(
    url,
    tahun,
    description,
    wilayah=None,
    url_2=None,
    url_3=None,
):
    """
    Dipanggil dari legacy_bridge / router Auto project.

    Semua sumber (Sumber_1..3) diproses dalam SATU panggilan:
    - Sumber_1 (Open Data) -> CSV multi-tahun multi-wilayah.
    - Sumber PDF tidak digunakan untuk mengisi nilai 3.a selama parser
      cakupan persentasenya belum terverifikasi. Grafik jumlah kasus tidak
      boleh dicampurkan dengan nilai persentase.

    Return:
        float bila satu nilai, atau dict {wilayah: nilai} bila multi.
    """
    tahun = int(tahun)

    source_urls = []
    for raw in (url, url_2, url_3):
        for part in re.split(
            r"[;\n]",
            str(raw or ""),
        ):
            part = part.strip()
            if part and part not in source_urls:
                source_urls.append(part)

    cache_key = tuple(source_urls)

    with _TBC_CACHE_LOCK:
        if cache_key in _ALL_DATA_CACHE:
            all_data = _ALL_DATA_CACHE[cache_key]
        else:
            print(
                f"    [TBC] Mengambil data cakupan penemuan kasus TB "
                f"dari {len(source_urls)} sumber..."
            )
            all_data = _build_all_data(source_urls)
            _ALL_DATA_CACHE[cache_key] = all_data

    if not all_data:
        raise RuntimeError(
            "Tidak ada data cakupan penemuan kasus TB yang berhasil "
            "diambil dari sumber yang diberikan."
        )

    lower = str(description or "").lower()

    wants_kab_kota = any(
        marker in lower
        for marker in (
            "kab/kota",
            "kabupaten/kota",
            "kabupaten dan kota",
            "seluruh kab",
            "semua kab",
            "kab kota",
        )
    )

    wants_provinsi = any(
        marker in lower
        for marker in (
            "prov. kalsel",
            "prov kalsel",
            "provinsi kalsel",
            "provinsi kalimantan selatan",
            "prov. kalimantan selatan",
            "dan prov",
        )
    )

    if wants_kab_kota:
        values = {
            wilayah_: data[tahun]
            for wilayah_, data in all_data.items()
            if tahun in data
        }

        if not wants_provinsi:
            values = {
                wilayah_: nilai
                for wilayah_, nilai in values.items()
                if wilayah_ != PROVINSI_TARGET
            }

    else:
        target = str(wilayah or "").strip() or PROVINSI_TARGET
        values = {
            wilayah_: data[tahun]
            for wilayah_, data in all_data.items()
            if wilayah_ == target and tahun in data
        }

    if not values:
        available = sorted(
            {
                tahun_
                for data in all_data.values()
                for tahun_ in data
            }
        )

        raise RuntimeError(
            f"Data cakupan penemuan kasus TB tahun {tahun} tidak "
            f"tersedia. Tahun tersedia: {available}."
        )

    if wants_kab_kota:
        # Dict agar label wilayah tiap baris benar di sheet.
        return values

    if len(values) == 1:
        return next(iter(values.values()))

    return values


__all__ = [
    "scrape_tbc",
]
