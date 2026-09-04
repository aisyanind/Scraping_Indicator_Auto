"""
Adapter INDEKS KERUKUNAN UMAT BERAGAMA (IKUB) - Auto project.

Sumber: Open Data Jabar (data.jabarprov.go.id) - CSV/ZIP yang sudah
difilter nama_provinsi = kalimantan selatan pada URL dari Master.

Port dari legacy `ikub.py` ke pola project Auto:
- URL Sumber_1 dipakai apa adanya (atau fallback sumber standar).
- Download ZIP berisi CSV (atau CSV langsung).
- Ekstrak {tahun: nilai} untuk Kalimantan Selatan.
- Hanya data provinsi tersedia; kab/kota diminta dilewati.

Output mengikuti pola IPK: nilai tunggal, atau dict {wilayah: nilai}.
"""

import csv
import io
import re
import zipfile

import requests

PROVINSI_TARGET = "Prov. Kalimantan Selatan"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}

# Sumber cadangan bila Sumber_1 bukan URL yang valid (data Kalsel).
DEFAULT_SOURCE = (
    "https://data.jabarprov.go.id/api-backend/bigdata/"
    "vertikalkementerian-2/"
    "od_34153_indeks_kerukunan_umat_beragama__prov_di_indonesia_v1"
    "?download=csvzip&limit=136&where={}"
    "&where_or={%22nama_provinsi%22:%22kalimantan%20selatan%22}"
)


class IkubUnavailableError(RuntimeError):
    """Data IKUB tidak tersedia untuk tahun yang diminta."""


def _parse_number_id(value):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        return None


def _download_csv_text(file_url):
    response = requests.get(
        file_url,
        headers=REQUEST_HEADERS,
        timeout=60,
    )
    response.raise_for_status()

    content_type = (
        response.headers.get("Content-Type", "").lower()
    )

    if (
        response.content.startswith(b"PK\x03\x04")
        or "zip" in content_type
        or str(file_url).lower().endswith(".zip")
    ):
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            csv_name = next(
                (
                    name
                    for name in archive.namelist()
                    if name.lower().endswith(".csv")
                ),
                None,
            )

            if not csv_name:
                raise RuntimeError(
                    "File ZIP IKUB tidak berisi file CSV."
                )

            with archive.open(csv_name) as csv_file:
                return csv_file.read().decode("utf-8")

    return response.text


def _parse_kalsel_by_year(csv_text):
    """
    Parse CSV -> {tahun: nilai} untuk Kalimantan Selatan.

    Header umum: id,kode_provinsi,nama_provinsi,indeks_kerukunan,satuan,tahun
    """
    first_line = (csv_text.split("\n")[0] or "").strip()
    delimiter = ";" if ";" in first_line else ","

    reader = csv.DictReader(
        io.StringIO(csv_text),
        delimiter=delimiter,
    )

    result = {}

    for row in reader:
        row_values = [
            str(value or "").strip().upper()
            for value in row.values()
            if value is not None
        ]

        if not any(
            "KALIMANTAN SELATAN" in value
            for value in row_values
        ):
            continue

        tahun = None
        nilai = None

        for key, value in row.items():
            if value is None:
                continue

            key_lower = str(key).lower()
            value_str = str(value).strip()

            if (
                "tahun" in key_lower
                and re.fullmatch(r"20\d{2}", value_str)
            ):
                tahun = int(value_str)
            elif (
                "indeks" in key_lower
                or "nilai" in key_lower
            ):
                parsed = _parse_number_id(value_str)
                if parsed is not None:
                    nilai = parsed

        if tahun is not None and nilai is not None:
            result[tahun] = nilai

    return result


def _normalize_region(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _resolve_targets(description, default_wilayah):
    """
    Tentukan daftar wilayah yang diminta dari teks Ambil_Data.
    IKUB hanya menyediakan data provinsi, jadi hanya provinsi yang
    bisa diambil; kab/kota diminta dilewati.
    """
    lower = str(description or "").lower()

    return [
        str(default_wilayah or "").strip()
        or PROVINSI_TARGET
    ]


def scrape_ikub(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Adapter IKUB untuk project Auto.

    Sumber dari Master dipakai; bila bukan URL valid, fallback ke
    DEFAULT_SOURCE. Hanya menyediakan data provinsi Kalimantan Selatan.
    """
    from sources.source_resolver import resolve_source_url

    tahun = int(tahun)

    source_url, _ = resolve_source_url(
        url,
        canonical=DEFAULT_SOURCE,
    )

    print(f"    [IKUB] Mengakses URL: {source_url}")

    csv_text = _download_csv_text(source_url)
    data_ikub = _parse_kalsel_by_year(csv_text)

    print(
        f"    [IKUB] Tahun tersedia di sumber: "
        f"{sorted(data_ikub.keys())}"
    )

    if tahun not in data_ikub:
        raise IkubUnavailableError(
            f"SOURCE_UNAVAILABLE: Data IKUB tahun {tahun} "
            "tidak tersedia pada sumber Jabar Open Data."
        )

    provinsi_value = data_ikub[tahun]

    targets = _resolve_targets(
        description,
        wilayah,
    )

    results = {}

    for target in targets:
        if (
            _normalize_region(target)
            == _normalize_region(PROVINSI_TARGET)
        ):
            results[target] = provinsi_value
        else:
            print(
                f"    [IKUB] Wilayah '{target}' tidak tersedia "
                "di sumber (hanya provinsi)."
            )

    if not results:
        raise RuntimeError(
            "Tidak ada wilayah IKUB yang bisa diekstrak "
            f"untuk tahun {tahun}."
        )

    if len(results) == 1:
        return next(iter(results.values()))

    return results


__all__ = ["scrape_ikub"]
