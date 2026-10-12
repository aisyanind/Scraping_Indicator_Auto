"""
Adapter SATU DATA KemenLH (satudata.kemenlh.go.id) — untuk IKLH dan dataset
publik lainnya.

Sumber: https://satudata.kemenlh.go.id/dataset/{slug}

Portal satudata KemenLH menyediakan API publik (tanpa login):
- GET /api/v1/portal/datasets -> daftar dataset + id asli + file
- GET /api/v1/datasets/{id}/table -> JSON tabel (publik)
- GET /api/v1/datasets/{id}/download.xlsx -> file XLSX (publik)

Alur adapter:
1. Resolve slug -> dataset id via portal/datasets (publik).
2. Cari dataset provinsi & dataset kab/kota terkait.
3. Ambil tabel via /api/v1/datasets/{id}/table (paginasi).
4. Deteksi tahun yang tersedia dari nama kolom.
5. Kembalikan nilai untuk scope & tahun yang diminta.
   Tahun yang tidak tersedia dilewati diam-diam (tidak berstatus gagal).
"""

import logging
import re
import threading
from io import BytesIO

import openpyxl
import requests

from sources.bps_utils import parse_number_id as _parse_index_number
from sources.region_utils import normalize_region

logging.getLogger("openpyxl").setLevel(logging.WARNING)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

PROVINSI_CANONICAL = "Prov. Kalimantan Selatan"

# Cache per slug dataset.
_PORTAL_CACHE = {}
_TABLE_CACHE = {}
_SATUDATA_LOCK = threading.Lock()


def _normalize_text(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s\-_/]+", " ", text)
    text = re.sub(r"[()\[\]{}.,;:]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _similar_score(left, right):
    """
    Skor kemiripan dua teks (0..1). Memakai token yang sama setelah
    normalisasi. Dua teks dianggap cocok jika semua token dari salah satu
    termuat di teks lainnya.
    """
    left = _normalize_text(left)
    right = _normalize_text(right)

    if not left or not right:
        return 0.0

    ltokens = set(left.split())
    rtokens = set(right.split())

    if not ltokens or not rtokens:
        return 0.0

    if ltokens <= rtokens or rtokens <= ltokens:
        return min(len(ltokens), len(rtokens)) / max(len(ltokens), len(rtokens))

    return 0.0


def _jaccard_score(left, right):
    """
    Skor Jaccard dua teks (intersection / union).
    Dipakai untuk mencari dataset pendamping (mis. "per Kabupaten/Kota")
    yang punya token ekstra.
    """
    ltokens = set(_normalize_text(left).split())
    rtokens = set(_normalize_text(right).split())

    if not ltokens or not rtokens:
        return 0.0

    union = ltokens | rtokens

    if not union:
        return 0.0

    return len(ltokens & rtokens) / len(union)


def _get_portal_datasets():
    """
    Ambil daftar dataset publik dari portal (cache per slug).
    """
    cache_key = "portal_datasets"

    with _SATUDATA_LOCK:
        if cache_key in _PORTAL_CACHE:
            return _PORTAL_CACHE[cache_key]

        url = "https://satudata.kemenlh.go.id/api/v1/portal/datasets"
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()
        datasets = data.get("datasets", [])

        # Index by slug (extracted from URL) dan id.
        by_slug = {}
        for ds in datasets:
            title = ds.get("title", "")
            ds_id = ds.get("id", "")
            by_slug[_normalize_text(title)] = ds
            by_slug[ds_id] = ds

        _PORTAL_CACHE[cache_key] = {
            "datasets": datasets,
            "by_slug": by_slug,
        }

        print(f"    [SATUDATA] {len(datasets)} dataset publik ditemukan.")
        return _PORTAL_CACHE[cache_key]


def _resolve_dataset(slug):
    """
    Cari dataset id dari slug URL atau title.
    Return dataset object atau None.
    """
    portal = _get_portal_datasets()
    by_slug = portal["by_slug"]

    norm = _normalize_text(slug)
    best_score = 0.0
    best = None

    for key, ds in by_slug.items():
        if _normalize_text(ds.get("id", "")) == norm:
            return ds

        score = _similar_score(norm, key)

        if score > best_score:
            best_score = score
            best = ds

    if best_score >= 0.6:
        return best

    return None


def _find_related_datasets(base_title):
    """
    Cari dataset provinsi & kab/kota yang terkait.
    IKLH punya 2 dataset: "Indeks Kualitas Lingkungan Hidup (IKLH)"
    dan "Indeks Kualitas Lingkungan Hidup per Kabupaten/Kota".
    """
    portal = _get_portal_datasets()
    datasets = portal["datasets"]

    base_norm = _normalize_text(base_title)
    prov = None
    kabkota = None

    for ds in datasets:
        title = ds.get("title", "")
        title_norm = _normalize_text(title)

        if _jaccard_score(base_norm, title_norm) < 0.5:
            continue

        if "kabupaten" in title_norm or "kabkota" in title_norm:
            kabkota = ds
        else:
            prov = ds

    return prov, kabkota


def _fetch_table(dataset_id):
    """
    Ambil tabel dataset via API publik (paginasi).
    Return list of rows (dicts).
    """
    all_rows = []
    page = 1

    while True:
        url = (
            f"https://satudata.kemenlh.go.id/api/v1/datasets/"
            f"{dataset_id}/table?page={page}&limit=100"
        )
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        data = response.json()

        rows = data.get("data", [])
        if not rows:
            break

        all_rows.extend(rows)

        total = data.get("total_data", 0)
        pages = data.get("pages", 1)

        if page >= pages:
            break

        page += 1

    return all_rows


def _download_xlsx(dataset_id):
    """
    Download XLSX sebagai fallback.
    Return workbook object.
    """
    url = (
        f"https://satudata.kemenlh.go.id/api/v1/datasets/"
        f"{dataset_id}/download.xlsx"
    )
    response = requests.get(url, headers=HEADERS, timeout=60)
    response.raise_for_status()
    return openpyxl.load_workbook(BytesIO(response.content))


def _extract_years_from_columns(rows):
    """
    Deteksi tahun yang tersedia dari nama kolom (mis. "IKLH 2024").
    Return set of int.
    """
    years = set()

    if not rows:
        return years

    for key in rows[0].keys():
        nums = re.findall(r"(20\d{2})", key)
        for n in nums:
            years.add(int(n))

    return years


def _extract_table_data(rows, region_col, value_col, tahun_target):
    """
    Ekstrak nilai untuk satu tahun dari tabel rows.
    Return {region_name: value} atau None jika tahun tidak tersedia.
    """
    result = {}

    for row in rows:
        region = str(row.get(region_col, "") or "").strip()
        raw = str(row.get(value_col, "") or "").strip()

        if not region or not raw:
            continue

        nilai = _parse_index_number(raw)
        if nilai is not None:
            result[region] = nilai

    return result


def _resolve_scope(description, wilayah):
    """
    Tentukan cakupan dari deskripsi + wilayah default.
    Return "ALL" / "KABKOTA" / "SPECIFIC" / "PROV".
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
        return "ALL"
    if wants_kab_kota:
        return "KABKOTA"

    # Wilayah spesifik yang bukan provinsi default.
    if wilayah and normalize_region(wilayah) != normalize_region(
        PROVINSI_CANONICAL
    ):
        return "SPECIFIC"

    return "PROV"


def _get_kalsel_province(rows):
    """
    Cari nilai Kalimantan Selatan dari tabel provinsi.
    Return {tahun: nilai} atau None.
    """
    tahun_cols = _extract_years_from_columns(rows)

    if not tahun_cols:
        return None

    for row in rows:
        prov = str(row.get("Provinsi", "") or "").strip()

        if "Kalimantan Selatan" not in prov:
            continue

        values = {}
        for tahun in sorted(tahun_cols):
            # Cari kolom yang mengandung tahun
            col_key = None
            for key in row.keys():
                if str(tahun) in key:
                    col_key = key
                    break

            if col_key:
                nilai = _parse_index_number(row.get(col_key))
                if nilai is not None:
                    values[tahun] = nilai

        return values

    return None


def _get_kalsel_kabkota(rows):
    """
    Cari nilai kab/kota Kalsel dari tabel kab/kota.
    Return {nama_kabkota: {tahun: nilai}}.
    """
    tahun_cols = _extract_years_from_columns(rows)

    if not tahun_cols:
        return None

    prov_col = None
    kabkota_col = None
    for key in rows[0].keys():
        kn = _normalize_text(key)
        if "provinsi" in kn:
            prov_col = key
        if "kabupaten" in kn or "kab/kota" in kn:
            kabkota_col = key

    if not prov_col or not kabkota_col:
        return None

    result = {}
    for row in rows:
        prov = str(row.get(prov_col, "") or "").strip()
        if "Kalimantan Selatan" not in prov:
            continue

        name = str(row.get(kabkota_col, "") or "").strip()
        if not name:
            continue

        values = {}
        for tahun in sorted(tahun_cols):
            col_key = None
            for key in row.keys():
                if str(tahun) in key:
                    col_key = key
                    break
            if col_key:
                nilai = _parse_index_number(row.get(col_key))
                if nilai is not None:
                    values[tahun] = nilai

        result[name] = values

    return result


def _canonical_region(source_name):
    """
    Map nama wilayah dari dataset ke format kanonik.
    """
    mapping = {
        "Kalimantan Selatan": PROVINSI_CANONICAL,
    }

    if source_name in mapping:
        return mapping[source_name]

    # kab/kota: "Kabupaten Tanah Laut" -> "Kab. Tanah Laut"
    text = str(source_name or "").strip()
    for old, new in (
        ("Kabupaten ", "Kab. "),
        ("Kota ", "Kota "),
    ):
        if text.startswith(old):
            text = new + text[len(old):]
            break

    return text


def _value_for_region(all_data, target_region, tahun):
    """
    Cari nilai untuk satu wilayah + tahun dari all_data.
    all_data = {canonical_region: {tahun: nilai}}
    Jika tahun tidak ada, return None (skip).
    """
    target_norm = normalize_region(target_region)

    for region, year_values in all_data.items():
        if normalize_region(region) != target_norm:
            continue

        return year_values.get(int(tahun))

    return None


def scrape_iklhd(url, tahun, description, wilayah=None):
    """
    Adapter dataset satudata.kemenlh.go.id — untuk IKLH dan dataset publik
    lainnya.

    Menemukan dataset dari slug URL, ambil tabel via API publik, lalu
    kembalikan nilai untuk provinsi + kab/kota Kalsel sesuai scope.

    Tahun yang tidak tersedia di dataset dilewati (bukan error).
    """
    tahun = int(tahun)

    # Ekstrak slug dari URL
    slug = url.rstrip("/").rsplit("/", 1)[-1] if url else ""

    if not slug:
        raise RuntimeError(
            "Tidak dapat mengekstrak slug dataset dari URL: "
            f"{url}"
        )

    print(f"    [SATUDATA] Slug: {slug}")

    # Resolve dataset
    dataset = _resolve_dataset(slug)
    if not dataset:
        raise RuntimeError(
            f"Dataset tidak ditemukan di portal: {slug}"
        )

    ds_id = dataset.get("id", "")
    title = dataset.get("title", "")

    print(f"    [SATUDATA] Dataset: {title} ({ds_id})")

    # Cari dataset terkait (provinsi + kab/kota)
    prov_ds, kabkota_ds = _find_related_datasets(title)

    if not prov_ds:
        raise RuntimeError(
            f"Dataset provinsi tidak ditemukan untuk: {title}"
        )

    # Ambil tabel
    prov_rows = _fetch_table(prov_ds["id"])
    prov_years = _extract_years_from_columns(prov_rows)

    kabkota_rows = []
    kabkota_years = set()
    if kabkota_ds:
        kabkota_rows = _fetch_table(kabkota_ds["id"])
        kabkota_years = _extract_years_from_columns(kabkota_rows)

    # Parse scope
    scope = _resolve_scope(description, wilayah)

    print(f"    [SATUDATA] Scope: {scope} | Tahun tersedia: prov={prov_years}, kabkota={kabkota_years}")

    # Bangun all_data
    all_data = {}

    # Provinsi
    prov_values = _get_kalsel_province(prov_rows)
    if prov_values:
        all_data[PROVINSI_CANONICAL] = prov_values

    # Kab/kota (dimuat selalu agar lookup SPECIFIC juga bisa)
    if kabkota_rows:
        kabkota_values = _get_kalsel_kabkota(kabkota_rows)
        if kabkota_values:
            for name, values in kabkota_values.items():
                canonical = _canonical_region(name)
                all_data[canonical] = values

    if not all_data:
        raise RuntimeError(
            "Tidak ada data Kalsel yang ditemukan pada dataset."
        )

    # Resolve target wilayah
    all_names = list(all_data.keys())
    named_prov = PROVINSI_CANONICAL
    kabkota_names = [n for n in all_names if n != named_prov]

    if scope == "PROV":
        targets = [named_prov]
    elif scope == "KABKOTA":
        targets = kabkota_names
    elif scope == "ALL":
        targets = [named_prov] + kabkota_names
    else:
        # SPECIFIC / default: gunakan wilayah yang diminta user
        targets = [str(wilayah or "").strip() or named_prov]

    # Ambil nilai per target
    values = {}
    missing = []

    for target in targets:
        val = _value_for_region(all_data, target, tahun)
        if val is not None:
            values[target] = val
        else:
            missing.append(target)

    if missing:
        print(
            f"    [SATUDATA] Tahun {tahun}: "
            f"{len(missing)} wilayah tidak ada data (skip)."
        )

    if not values:
        # Tahun tidak tersedia — skip diam-diam
        raise RuntimeError(
            f"SOURCE_UNAVAILABLE: IKLH tahun {tahun} belum tersedia "
            f"di portal satudata."
        )

    if len(values) == 1:
        return next(iter(values.values()))

    return values