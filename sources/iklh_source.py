import csv
import re
from io import StringIO
from urllib.parse import urlparse

import requests


NAMA_PROVINSI = "Prov. Kalimantan Selatan"

# Cache dipisahkan per URL sumber agar aman bila nanti dataset berubah.
_TABLE_CACHE = {}


def _normalize_wilayah(value):
    text = re.sub(
        r"\s+",
        " ",
        str(value or "").strip().lower(),
    )

    replacements = (
        ("provinsi ", "prov. "),
        ("prov ", "prov. "),
        ("kabupaten ", "kab. "),
        ("kab ", "kab. "),
    )

    for old, new in replacements:
        if text.startswith(old):
            text = new + text[len(old):]
            break

    return text


def _parse_index_number(value):
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


def _extract_dataset_id(url):
    """
    Ambil UUID dataset dari URL Open Data Kalsel.

    Contoh:
    https://opendata.kalselprov.go.id/open-data/dataset/
    b43f5eef-565f-43f8-9f80-0aa9e0c079eb
    """
    match = re.search(
        r"([0-9a-fA-F]{8}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{12})",
        str(url or ""),
    )

    return match.group(1) if match else None


def _build_download_url(source_url):
    """
    Tidak ada URL dataset yang di-hard-code.

    Aturan:
    1. Jika user sudah memasukkan endpoint CSV/download langsung,
       pakai URL itu apa adanya.
    2. Jika user memasukkan halaman dataset Open Data Kalsel,
       ambil dataset ID dari URL lalu bentuk endpoint download API.
    3. Selain itu, coba URL user apa adanya.
    """
    source_url = str(source_url or "").strip()

    if not source_url:
        raise RuntimeError(
            "URL sumber IKLH kosong."
        )

    lower = source_url.lower()

    if (
        "response_type=csv" in lower
        or lower.endswith(".csv")
        or "/dataset/download/" in lower
    ):
        return source_url

    dataset_id = _extract_dataset_id(
        source_url
    )

    if (
        dataset_id
        and "opendata.kalselprov.go.id" in lower
    ):
        return (
            "https://opendata.kalselprov.go.id/"
            "api/v1/apps/dataset/download/"
            f"{dataset_id}?response_type=csv"
        )

    return source_url


def _request_headers(source_url):
    """
    Header dibentuk dinamis dari URL sumber.
    Tidak ada Referer dataset tertentu yang ditanam di kode.
    """
    parsed = urlparse(
        str(source_url or "")
    )

    origin = ""
    if parsed.scheme and parsed.netloc:
        origin = (
            f"{parsed.scheme}://{parsed.netloc}"
        )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/csv,application/json,text/plain,*/*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    if origin:
        headers["Origin"] = origin
        headers["Referer"] = source_url

    return headers


def _download_iklh_csv(source_url):
    download_url = _build_download_url(
        source_url
    )

    print(
        f"    [IKLH AUTO] Sumber dari Master: {source_url}"
    )
    print(
        f"    [IKLH AUTO] Download CSV: {download_url}"
    )

    response = requests.get(
        download_url,
        headers=_request_headers(source_url),
        timeout=60,
    )
    response.raise_for_status()

    text = response.text

    # Deteksi sederhana agar halaman HTML tidak salah diparse sebagai CSV.
    content_type = (
        response.headers
        .get("Content-Type", "")
        .lower()
    )

    if (
        "text/html" in content_type
        and "<html" in text[:500].lower()
    ):
        raise RuntimeError(
            "URL sumber mengembalikan HTML, bukan CSV. "
            "Pastikan URL menunjuk ke halaman dataset Open Data Kalsel "
            "yang memiliki ID dataset, atau endpoint download CSV."
        )

    return text


def _parse_iklh_table(csv_text):
    """
    Parser sumber lama dipertahankan:
    CSV menggunakan delimiter semicolon.
    """
    reader = csv.reader(
        StringIO(csv_text),
        delimiter=";",
    )
    rows = list(reader)

    if not rows:
        raise RuntimeError(
            "CSV IKLH kosong."
        )

    header = [
        str(cell or "").strip()
        for cell in rows[0]
    ]

    year_columns = {}

    for index, column_name in enumerate(
        header
    ):
        if re.fullmatch(
            r"\d{4}",
            column_name,
        ):
            year_columns[
                int(column_name)
            ] = index

    if not year_columns:
        raise RuntimeError(
            "Tidak menemukan kolom tahun pada CSV IKLH. "
            f"Header terbaca: {header}"
        )

    try:
        kd_wil_index = header.index(
            "Kd_wil"
        )
        nama_index = header.index(
            "Kabupaten/kota"
        )

    except ValueError as error:
        raise RuntimeError(
            f"Kolom wajib IKLH tidak ditemukan: {error}. "
            f"Header terbaca: {header}"
        )

    result = {}

    for row in rows[1:]:
        if (
            not row
            or len(row)
            <= max(
                kd_wil_index,
                nama_index,
            )
        ):
            continue

        kd_wil = str(
            row[kd_wil_index] or ""
        ).strip()

        nama = str(
            row[nama_index] or ""
        ).strip()

        if not kd_wil or not nama:
            continue

        data_per_tahun = {}

        for tahun, column_index in year_columns.items():
            if column_index >= len(row):
                continue

            nilai = _parse_index_number(
                row[column_index]
            )

            if nilai is not None:
                data_per_tahun[
                    tahun
                ] = nilai

        result[kd_wil] = {
            "nama": nama,
            "data": data_per_tahun,
        }

    if not result:
        raise RuntimeError(
            "Tabel IKLH berhasil dibaca tetapi tidak ada data."
        )

    return result


def _get_table(source_url):
    cache_key = str(
        source_url or ""
    ).strip()

    if not cache_key:
        raise RuntimeError(
            "URL sumber IKLH kosong."
        )

    if cache_key not in _TABLE_CACHE:
        csv_text = _download_iklh_csv(
            cache_key
        )

        _TABLE_CACHE[
            cache_key
        ] = _parse_iklh_table(
            csv_text
        )

        print(
            f"    [IKLH AUTO] "
            f"{len(_TABLE_CACHE[cache_key])} wilayah ditemukan."
        )

    return _TABLE_CACHE[
        cache_key
    ]


def _get_value(
    table,
    nama_wilayah,
    tahun,
):
    target = _normalize_wilayah(
        nama_wilayah
    )

    for info in table.values():
        if (
            _normalize_wilayah(
                info["nama"]
            )
            != target
        ):
            continue

        nilai = info["data"].get(
            int(tahun)
        )

        if nilai is None:
            raise RuntimeError(
                f"Data IKLH '{nama_wilayah}' "
                f"tahun {tahun} tidak tersedia."
            )

        return nilai

    raise RuntimeError(
        f"Wilayah '{nama_wilayah}' "
        "tidak ditemukan pada sumber IKLH."
    )


def _resolve_scope(
    table,
    description,
    wilayah,
):
    lower = str(
        description or ""
    ).lower()

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

    all_names = [
        info["nama"]
        for info in table.values()
    ]

    kab_kota_names = [
        name
        for name in all_names
        if _normalize_wilayah(name)
        != _normalize_wilayah(
            NAMA_PROVINSI
        )
    ]

    if (
        wants_kab_kota
        and wants_provinsi
    ):
        return [
            NAMA_PROVINSI
        ] + kab_kota_names

    if wants_kab_kota:
        return kab_kota_names

    return [
        str(
            wilayah
            or NAMA_PROVINSI
        ).strip()
        or NAMA_PROVINSI
    ]


def scrape_iklh(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Pure source scraper untuk project Auto.

    URL sumber WAJIB berasal dari Master/Form.
    Tidak ada DATASET_ID / DOWNLOAD_URL tertentu yang di-hard-code.

    Tidak membaca Master lama.
    Tidak membaca Hasil lama.
    Tidak mengirim ke Apps Script produksi.

    Return:
      single wilayah -> float
      multi wilayah  -> {wilayah: float, ...}
    """
    source_url = str(
        url or ""
    ).strip()

    # IKLH Open Data Kalsel tidak punya sumber kanonik tunggal — URL dari
    # user wajib valid. Jika bukan URL, beri status LINK_TIDAK_LENGKAP.
    from sources.source_resolver import resolve_source_url

    source_url, _ = resolve_source_url(source_url)

    if not source_url:
        raise RuntimeError(
            "URL sumber IKLH kosong."
        )

    table = _get_table(
        source_url
    )

    targets = _resolve_scope(
        table,
        description,
        wilayah,
    )

    print(
        f"    [IKLH AUTO] Tahun {tahun} | "
        f"target {len(targets)} wilayah."
    )

    values = {}
    errors = []

    for target in targets:
        try:
            values[target] = _get_value(
                table,
                target,
                int(tahun),
            )

        except Exception as error:
            errors.append(
                f"{target}: "
                f"{str(error).splitlines()[0]}"
            )

    if not values:
        raise RuntimeError(
            "Tidak ada nilai IKLH yang berhasil diekstrak. "
            + "; ".join(errors[:5])
        )

    if errors:
        print(
            f"    [IKLH AUTO] WARNING: "
            f"{len(errors)} wilayah gagal."
        )

    if len(values) == 1:
        return next(
            iter(values.values())
        )

    return values
