"""
Adapter CAKUPAN PENEMUAN KASUS TUBERKULOSIS (Auto project).

Port dari modul legacy `tbc.py`:
- Sumber_1 (opendata.kalselprov.go.id dataset) -> CSV multi-tahun
  multi-wilayah (provinsi + kab/kota).
- Sumber_2 (PDF Profil Kesehatan via Google Drive) -> nilai provinsi
  satu tahun, dipakai hanya bila Sumber_1 tidak memuat tahun/wilayah tsb.
- Return berupa dict {wilayah: nilai} bila multi-wilayah, atau float
  bila satu wilayah, sesuai konvensi run_indicator.
"""

import csv
import io
import re
import urllib.request
from urllib.parse import urljoin

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
_ALL_DATA_CACHE = {}


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


def get_sumber2_data(gdrive_share_url):
    """
    Return:
        (tahun, nilai_provinsi)

    Sumber PDF ini hanya dipakai untuk nilai provinsi.
    """

    direct_url = resolve_gdrive_direct_url(
        gdrive_share_url
    )

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

    if (
        len(content) < 4
        or content[:4] != b"%PDF"
    ):
        raise RuntimeError(
            "File Sumber_2 yang diterima bukan PDF valid."
        )

    result = (None, None)

    with pdfplumber.open(
        io.BytesIO(content)
    ) as pdf:

        for page in pdf.pages:
            text = (
                page.extract_text()
                or ""
            )

            if (
                "CAKUPAN PENEMUAN KASUS "
                "TUBERKULOSIS"
                not in text.upper()
            ):
                continue

            tahun_match = re.search(
                r"TAHUN\s+(\d{4})",
                text,
                flags=re.IGNORECASE,
            )

            tahun = (
                int(tahun_match.group(1))
                if tahun_match
                else None
            )

            for line in text.split("\n"):
                line_upper = line.upper()

                if (
                    "CAKUPAN PENEMUAN KASUS "
                    "TUBERKULOSIS"
                    in line_upper
                    and
                    "ANAK" not in line_upper
                ):
                    angka = re.findall(
                        r"[\d.,]+",
                        line,
                    )

                    if not angka:
                        continue

                    nilai = parse_number_id(
                        angka[-1]
                    )

                    if (
                        nilai is not None
                        and tahun is not None
                    ):
                        print(
                            "    [TBC] [SUMBER 2] Provinsi "
                            f"tahun {tahun} = {nilai}%"
                        )

                        result = (
                            tahun,
                            nilai,
                        )
                        break

            if result != (None, None):
                break

    if result == (None, None):
        print(
            "    [TBC] [SUMBER 2] Baris Cakupan Penemuan "
            "Kasus Tuberkulosis tidak ditemukan."
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
    Return:
        {wilayah: {tahun: nilai}}

    URL opendata -> CSV multi-tahun multi-wilayah;
    URL lain (Google Drive / PDF) -> nilai provinsi satu tahun.
    """
    all_data = {}

    for index, source_url in enumerate(source_urls, start=1):
        source_url = str(source_url or "").strip()

        if not source_url:
            continue

        try:
            if "opendata.kalselprov.go.id" in source_url.lower():
                per_tahun = get_sumber1_data(source_url)

                for tahun, per_wilayah in per_tahun.items():
                    for wilayah, nilai in per_wilayah.items():
                        all_data.setdefault(
                            wilayah,
                            {},
                        ).setdefault(
                            int(tahun),
                            nilai,
                        )

            else:
                tahun, nilai = get_sumber2_data(source_url)

                if (
                    nilai is not None
                    and tahun is not None
                ):
                    all_data.setdefault(
                        PROVINSI_TARGET,
                        {},
                    ).setdefault(
                        int(tahun),
                        nilai,
                    )

        except Exception as error:
            print(
                f"    [TBC] Sumber {index} gagal: "
                f"{str(error).splitlines()[0][:200]}"
            )

    return all_data


_CSV_CACHE = {}


def get_sumber1_data(dataset_page_url):
    cache_key = str(dataset_page_url or "").strip()

    if cache_key in _CSV_CACHE:
        return _CSV_CACHE[cache_key]

    csv_url = resolve_opendata_csv_url(
        dataset_page_url
    )

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
    - Sumber_1 (opendata) -> CSV multi-tahun multi-wilayah.
    - Sumber_2/3 (PDF Profil Kesehatan) -> nilai provinsi satu tahun,
      hanya memuat data yang belum ada di Sumber_1.

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
