"""
Adapter INDEKS PEMBANGUNAN KEBUDAYAAN (IPK) - Auto project.

Port dari modul legacy `ipk.py`:
- Sumber: tabel HTML https://ipk.kemenbud.go.id/provinsi/{kode}
- Tabel berisi kolom Tahun + Nasional + Provinsi (KALIMANTAN SELATAN).
- Tidak ada data per Kab/Kota pada sumber ini, sehingga hanya provinsi
  yang dapat diambil. Wilayah lain yang diminta di deskripsi dilewati
  (bukan error) sesuai kebijakan "kalau ga ada ya udah ga ada".
"""

import re

import requests
from bs4 import BeautifulSoup

from config import REQUEST_HEADERS
from sources.bps_utils import parse_number_id as _parse_number_id
from sources.region_utils import normalize_region, KALSEL_KAB_KOTA

PROVINSI_TARGET = "Prov. Kalimantan Selatan"

# Sumber standar bila kolom Sumber_1 diisi nama/teks (bukan URL).
DEFAULT_SOURCE = "https://ipk.kemenbud.go.id/provinsi/63"

class IpkUnavailableError(RuntimeError):
    """Data IPK tidak tersedia untuk tahun yang diminta."""


def _prov_norm(value):
    return normalize_region(value)


def get_sumber_html_data(url):
    """
    Ambil data IPK dari tabel HTML web Kemenbud.

    Return format: { 2018: 53.79, 2019: 55.99, ... }
    """
    print(f"    [IPK] Mengakses URL: {url}")

    response = requests.get(
        url,
        headers=REQUEST_HEADERS,
        timeout=60,
    )
    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser",
    )

    hasil_provinsi = {}

    for table in soup.find_all("table"):
        headers = [
            th.get_text(strip=True).upper()
            for th in table.find_all("th")
        ]

        if "TAHUN" not in headers:
            continue

        if not any(
            "KALIMANTAN SELATAN" in header
            for header in headers
        ):
            continue

        idx_tahun = headers.index("TAHUN")
        idx_kalsel = next(
            index
            for index, header in enumerate(headers)
            if "KALIMANTAN SELATAN" in header
        )

        rows = table.find_all("tr")

        for row in rows:
            cols = row.find_all("td")

            if len(cols) <= max(idx_tahun, idx_kalsel):
                continue

            tahun_str = cols[idx_tahun].get_text(strip=True)
            nilai_str = cols[idx_kalsel].get_text(strip=True)

            if not re.fullmatch(r"\d{4}", tahun_str):
                continue

            nilai = _parse_number_id(nilai_str)

            if nilai is not None:
                hasil_provinsi[int(tahun_str)] = nilai

        break

    if not hasil_provinsi:
        raise RuntimeError(
            "Tidak ada data IPK Kalimantan Selatan "
            f"yang berhasil diekstrak dari: {url}"
        )

    return hasil_provinsi


def _resolve_targets(description, default_wilayah):
    """
    Tentukan daftar wilayah yang diminta dari teks Ambil_Data.

    Mengikuti pola AKI: deteksi kata kunci kab/kota & provinsi.
    """
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
            "provinsi +",
        )
    )

    if wants_kab_kota and wants_provinsi:
        return [PROVINSI_TARGET] + KALSEL_KAB_KOTA

    if wants_kab_kota:
        return KALSEL_KAB_KOTA

    return [
        str(default_wilayah or "").strip()
        or PROVINSI_TARGET
    ]


def scrape_ipk(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Adapter IPK untuk project Auto.

    Sumber hanya menyediakan data provinsi. Wilayah lain (kab/kota)
    yang diminta di deskripsi dilewati tanpa error.
    """
    tahun = int(tahun)

    # Kolom Sumber_1 kadang diisi nama indikator, bukan URL. Jika bukan URL
    # yang valid, gunakan sumber standar IPK Kemenbud (provinsi Kalsel).
    from sources.source_resolver import resolve_source_url

    source_url, _ = resolve_source_url(
        url,
        canonical=DEFAULT_SOURCE,
    )

    data_ipk = get_sumber_html_data(source_url)

    if tahun not in data_ipk:
        raise IpkUnavailableError(
            f"SOURCE_UNAVAILABLE: Data IPK tahun {tahun} "
            "tidak tersedia pada sumber Kemenbud."
        )

    provinsi_value = data_ipk[tahun]
    targets = _resolve_targets(
        description,
        wilayah,
    )

    prov_norm = _prov_norm(PROVINSI_TARGET)
    results = {}

    for target in targets:
        if _prov_norm(target) == prov_norm:
            results[target] = provinsi_value

    if not results:
        print(
            f"    [IPK] Tahun {tahun}: wilayah yang diminta "
            "tidak tersedia di sumber (hanya provinsi)."
        )
        return {}

    if len(results) == 1:
        return next(iter(results.values()))

    return results
