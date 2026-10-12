"""
Adapter TST - Timbulan Sampah Terolah di Fasilitas Pengolahan Sampah (%).

Sumber: SIPSN Kementerian Lingkungan Hidup
    https://sampahnasional.kemenlh.go.id/portal-indikatif/data/capaian

Backend API (ditemukan dari config Nuxt frontend):
    GET https://sampahnasional.kemenlh.go.id/indikatif/api/v2/tbl_capaian
    header key: <backendKey>
    param  lmt: 99999  (ambil seluruh baris)

Kolom yang relevan:
    nama_provinsi   - provinsi
    nama_kabkota    - kabupaten/kota
    tahun           - tahun data
    persen_penanganan - % sampah terolah di fasilitas pengolahan
    persen_kelola   - % sampah terkelola (pengurangan + penanganan)

Catatan: API ini hanya menyediakan kab/kota (tidak ada baris provinsi),
sesuai kebijakan "kalau ga ada ya udah ga ada" provinsi di-skip.
"""

import requests

from config import REQUEST_HEADERS
from sources.region_utils import normalize_region, KALSEL_KAB_KOTA

TST_API_URL = (
    "https://sampahnasional.kemenlh.go.id/"
    "indikatif/api/v2/tbl_capaian"
)

# backendKey diambil dari config Nuxt halaman sampahnasional.
TST_API_KEY = "80kkc8kw88w40cg8osc84c4ocg4kgks840ck8s4g"

KALSEL_PROVINSI = [
    "Prov. Kalimantan Selatan",
]

KALSEL_ALL = KALSEL_PROVINSI + KALSEL_KAB_KOTA


class TstUnavailableError(RuntimeError):
    """Data TST tidak tersedia untuk tahun yang diminta."""


def _resolve_targets(description, default_wilayah):
    """
    Tentukan daftar wilayah yang diminta dari teks Ambil_Data.
    Mengikuti pola adapter lain (kab/kota & provinsi).
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
        return KALSEL_ALL

    if wants_kab_kota:
        return KALSEL_KAB_KOTA

    return [
        str(default_wilayah or "").strip()
        or KALSEL_PROVINSI[0]
    ]


def _fetch_capaian_all():
    """
    Ambil seluruh baris tabel capaian SIPSN.

    Return list of dict rows.
    """
    headers = dict(REQUEST_HEADERS)
    headers["key"] = TST_API_KEY
    headers["Accept"] = "application/json"

    response = requests.get(
        TST_API_URL,
        headers=headers,
        params={
            "key": TST_API_KEY,
            "lmt": 99999,
        },
        timeout=60,
    )
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Response API TST bukan object JSON."
        )

    if payload.get("status") is False:
        raise RuntimeError(
            "API TST error: "
            + str(payload.get("error", "Unknown error"))
        )

    rows = payload.get("data", []) or []

    if not rows:
        raise RuntimeError(
            "API TST tidak mengembalikan data."
        )

    return rows


def _map_kalsel(rows, tahun):
    """
    Pilih baris Kalimantan Selatan dan peta nama kab/kota ke nilai
    persen penanganan (terolah di fasilitas pengolahan).

    Hanya baris dengan tahun yang diminta yang dipetakan.

    Return {nama_kabkota: nilai}
    """
    result = {}
    tahun_str = str(tahun)

    for row in rows:
        if str(row.get("tahun", "") or "").strip() != tahun_str:
            continue

        provinsi = str(row.get("nama_provinsi", "") or "").strip()

        if "Kalimantan Selatan" not in provinsi:
            continue

        kabkota = str(row.get("nama_kabkota", "") or "").strip()

        if not kabkota:
            continue

        try:
            nilai = float(row.get("persen_penanganan"))
        except (TypeError, ValueError):
            continue

        result[kabkota] = nilai

    return result


def scrape_tst(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Adapter TST untuk project Auto.

    Return dict {wilayah: nilai} berisi kab/kota yang datanya
    tersedia di SIPSN. Provinsi tidak tersedia di API ini,
    sehingga dilewati (kalau ga ada ya udah ga ada).
    """
    tahun = int(tahun)

    rows = _fetch_capaian_all()

    years = {
        str(row.get("tahun", "")).strip()
        for row in rows
    }

    if str(tahun) not in years:
        raise TstUnavailableError(
            f"SOURCE_UNAVAILABLE: Data TST tahun {tahun} "
            "tidak tersedia pada SIPSN. "
            f"Tahun tersedia: {sorted(years)}"
        )

    available = _map_kalsel(rows, tahun)

    targets = _resolve_targets(
        description,
        wilayah,
    )

    results = {}

    for target in targets:
        target_norm = normalize_region(target)

        for kabkota, nilai in available.items():
            if normalize_region(kabkota) == target_norm:
                results[target] = nilai
                break

    if not results:
        print(
            f"    [TST] Tahun {tahun}: tidak ada kab/kota "
            "Kalimantan Selatan yang tersedia di sumber."
        )
        return {}

    print(
        f"    [TST] Berhasil {len(results)} kab/kota "
        f"untuk tahun {tahun}."
    )

    if len(results) == 1:
        return next(iter(results.values()))

    return results
