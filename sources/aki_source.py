import logging
import re
import threading
from html import unescape
from io import BytesIO
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

import pdfplumber
import requests


logging.getLogger("pdfminer").setLevel(logging.ERROR)

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

PROVINSI_CANONICAL = "Prov. Kalimantan Selatan"

REGION_ALIASES = {
    "Kalimantan Selatan": "Prov. Kalimantan Selatan",
    "Kabupaten Tanah Laut": "Kab. Tanah Laut",
    "Kabupaten Kotabaru": "Kab. Kotabaru",
    "Kabupaten Banjar": "Kab. Banjar",
    "Kabupaten Barito Kuala": "Kab. Barito Kuala",
    "Kabupaten Tapin": "Kab. Tapin",
    "Kabupaten Hulu Sungai Selatan": "Kab. Hulu Sungai Selatan",
    "Kabupaten Hulu Sungai Tengah": "Kab. Hulu Sungai Tengah",
    "Kabupaten Hulu Sungai Utara": "Kab. Hulu Sungai Utara",
    "Kabupaten Tabalong": "Kab. Tabalong",
    "Kabupaten Tanah Bumbu": "Kab. Tanah Bumbu",
    "Kabupaten Balangan": "Kab. Balangan",
    "Kota Banjarmasin": "Kota Banjarmasin",
    "Kota Banjarbaru": "Kota Banjarbaru",
}

KAB_KOTA_SOURCE = (
    "Kabupaten Tanah Laut",
    "Kabupaten Kotabaru",
    "Kabupaten Banjar",
    "Kabupaten Barito Kuala",
    "Kabupaten Tapin",
    "Kabupaten Hulu Sungai Selatan",
    "Kabupaten Hulu Sungai Tengah",
    "Kabupaten Hulu Sungai Utara",
    "Kabupaten Tabalong",
    "Kabupaten Tanah Bumbu",
    "Kabupaten Balangan",
    "Kota Banjarmasin",
    "Kota Banjarbaru",
)

CHART_REGION_ORDER = KAB_KOTA_SOURCE + (
    "Kalimantan Selatan",
)

# Cache dipisahkan per URL sumber dari Master.
_ALL_DATA_CACHE = {}

# Cadangan bila sumber dari Master gagal/memblokir scraper (mis. HTTP 403).
FALLBACK_SOURCES = (
    "https://dinkeskalsel.id/?content=list&type=download",
)

# Sumber yang sudah terbukti gagal; jangan dicoba ulang per-tahun.
_FAILED_SOURCES = set()

# Kunci thread untuk cache & _FAILED_SOURCES (dipakai intra-job paralel).
_AKI_LOCK = threading.Lock()


def _normalize_text(value):
    return re.sub(
        r"\s+",
        " ",
        str(value or "").strip().lower(),
    )


def _normalize_region(value):
    text = _normalize_text(value)

    for old, new in (
        ("provinsi ", "prov. "),
        ("prov ", "prov. "),
        ("kabupaten ", "kab. "),
        ("kab ", "kab. "),
    ):
        if text.startswith(old):
            text = new + text[len(old):]
            break

    if text == "kalimantan selatan":
        text = "prov. kalimantan selatan"

    return text


def _canonical_region(source_name):
    return REGION_ALIASES.get(
        source_name,
        source_name,
    )


def _is_pdf_url(url):
    path = unquote(urlparse(str(url)).path).lower()
    return path.endswith(".pdf")


def _set_page_parameter(url, page):
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(int(page))

    return urlunparse(
        parsed._replace(
            query=urlencode(query)
        )
    )


def _parse_pdf_links(html, page_url):
    """
    Ambil link Profil Kesehatan dari halaman sumber yang DIISI USER.
    Tidak lagi memakai domain/link hard-code.
    """
    links = re.findall(
        r'(?:href|data-href)\s*=\s*["\']([^"\']+)["\']',
        html,
        re.I,
    )

    result = []

    for raw_link in links:
        link = unescape(raw_link).strip()
        decoded_link = unquote(link).lower()

        if not re.search(
            r"\.pdf(?:[?#]|$)",
            decoded_link,
            re.I,
        ):
            continue

        # Tetap prioritaskan publikasi Profil Kesehatan seperti parser lama.
        if (
            "profil" not in decoded_link
            or "kesehatan" not in decoded_link
        ):
            continue

        absolute_link = urljoin(
            page_url,
            link,
        )

        if absolute_link not in result:
            result.append(absolute_link)

    return result


def _get_pdf_links(source_url):
    source_url = str(source_url or "").strip()

    if not source_url:
        raise RuntimeError(
            "Sumber AKI kosong. Isi URL sumber pada Master/Form."
        )

    # Bila user memberi link PDF langsung, tidak perlu scan halaman daftar.
    if _is_pdf_url(source_url):
        print("    [AKI AUTO] Sumber berupa PDF langsung.")
        return [source_url]

    semua = []
    consecutive_empty = 0

    # Pertahankan perilaku parser lama: halaman daftar bisa berpaginasi.
    for page in range(1, 20):
        page_url = _set_page_parameter(
            source_url,
            page,
        )

        print(
            f"    [AKI AUTO] Scan sumber page={page}: "
            f"{page_url}"
        )

        response = requests.get(
            page_url,
            headers=HEADERS,
            timeout=30,
        )
        response.raise_for_status()

        page_links = _parse_pdf_links(
            response.text,
            page_url,
        )

        if not page_links:
            consecutive_empty += 1
        else:
            consecutive_empty = 0

        for link in page_links:
            if link not in semua:
                semua.append(link)

        # Setelah beberapa halaman kosong berturut-turut, hentikan scan.
        if page >= 3 and consecutive_empty >= 3:
            break

    if not semua:
        raise RuntimeError(
            "Tidak menemukan link PDF Profil Kesehatan dari URL sumber "
            f"yang diberikan: {source_url}"
        )

    return semua


def _download_pdf(url):
    response = requests.get(
        url,
        headers=HEADERS,
        timeout=120,
    )
    response.raise_for_status()

    if not response.content.startswith(b"%PDF"):
        raise RuntimeError(
            f"URL tidak mengembalikan PDF valid: {url}"
        )

    return BytesIO(response.content)


def _get_report_year(url, fallback_year=None):
    decoded_url = unquote(url).upper()

    for pattern in (
        r"TAHUN[^0-9]*(20\d{2})",
        r"(20\d{2})",
    ):
        match = re.search(
            pattern,
            decoded_url,
        )

        if match:
            return int(match.group(1))

    if fallback_year is not None:
        return int(fallback_year)

    return None


def _extract_aki_by_year(pdf):
    values = {}

    # Mempertahankan pola sumber lama: angka AKI per 100.000 kelahiran hidup.
    pattern = re.compile(
        r"(?:pada|tahun)\s+(20\d{2}).{0,180}?"
        r"(?:mencapai|sebesar)\s+"
        r"(\d+(?:[.,]\d+)?)\s+per\s+100\.000",
        re.IGNORECASE | re.DOTALL,
    )

    with pdfplumber.open(pdf) as document:
        for page_number, page in enumerate(
            document.pages,
            start=1,
        ):
            try:
                text = page.extract_text(layout=False) or ""
            except Exception as error:
                logging.warning(
                    "Gagal membaca halaman %s: %s",
                    page_number,
                    error,
                )
                continue

            if "KEMATIAN IBU" not in text.upper():
                continue

            normalized = " ".join(text.split())

            for match in pattern.finditer(normalized):
                year = int(match.group(1))
                value = float(
                    match.group(2).replace(",", ".")
                )

                values[year] = (
                    int(value)
                    if value.is_integer()
                    else value
                )

    return values


def _extract_aki_by_region(pdf, report_year):
    chart_values = []

    with pdfplumber.open(pdf) as document:
        for page in document.pages:
            page_chart_values = []

            try:
                words = page.extract_words()
            except Exception:
                continue

            page_text = " ".join(
                word["text"]
                for word in words
            ).upper()

            if "ANGKA KEMATIAN IBU" not in page_text:
                continue

            if "KAB/KOTA" not in page_text:
                continue

            title_word = next(
                (
                    word
                    for word in words
                    if word["text"].upper() == "KAB/KOTA"
                ),
                None,
            )

            if not title_word:
                continue

            source_word = next(
                (
                    word
                    for word in words
                    if word["text"].upper() == "SUMBER:"
                    and word["top"] > title_word["top"]
                ),
                None,
            )

            if not source_word:
                source_word = next(
                    (
                        word
                        for word in words
                        if word["text"].upper() in ("GAMBAR", "FIGURE")
                        and word["top"] > title_word["top"]
                    ),
                    None,
                )

            if not source_word:
                continue

            for word in words:
                if word["x0"] <= 140:
                    continue

                if not (
                    title_word["top"] + 10
                    < word["top"]
                    < source_word["top"]
                ):
                    continue

                if not re.fullmatch(
                    r"\d{1,3}",
                    word["text"],
                ):
                    continue

                value = int(word["text"])

                if value == 0:
                    continue

                if 0 <= value <= 500:
                    page_chart_values.append(
                        (word["x0"], value)
                    )

            if len(page_chart_values) >= len(
                KAB_KOTA_SOURCE
            ):
                chart_values = page_chart_values
                break

    chart_values.sort(
        key=lambda item: item[0]
    )

    if len(chart_values) < len(
        KAB_KOTA_SOURCE
    ):
        return {}

    region_order = (
        CHART_REGION_ORDER
        if len(chart_values) >= len(CHART_REGION_ORDER)
        else KAB_KOTA_SOURCE
    )

    chart_values = chart_values[
        -len(region_order):
    ]

    return {
        _canonical_region(region): {
            report_year: value
        }
        for region, (_, value)
        in zip(region_order, chart_values)
    }


def _build_all_data(source_url, requested_year=None):
    results = {}

    pdf_links = _get_pdf_links(
        source_url
    )

    print(
        f"    [AKI AUTO] "
        f"{len(pdf_links)} PDF Profil Kesehatan ditemukan."
    )

    for pdf_url in pdf_links:
        report_year = _get_report_year(
            pdf_url,
            fallback_year=(
                requested_year
                if len(pdf_links) == 1
                else None
            ),
        )

        if report_year is None:
            print(
                "    [AKI AUTO] Tahun laporan tidak terbaca, "
                f"lewati: {pdf_url}"
            )
            continue

        print(
            f"    [AKI AUTO] Proses Profil Kesehatan "
            f"{report_year}"
        )

        try:
            pdf = _download_pdf(
                pdf_url
            )

            province_values = _extract_aki_by_year(
                pdf
            )

            pdf.seek(0)

            region_values = _extract_aki_by_region(
                pdf,
                report_year,
            )

        except Exception as error:
            print(
                f"    [AKI AUTO] WARNING PDF {report_year} gagal: "
                f"{str(error).splitlines()[0]}"
            )
            continue

        for year, value in province_values.items():
            target = results.setdefault(
                PROVINSI_CANONICAL,
                {},
            )
            target.setdefault(
                int(year),
                value,
            )

        for region, region_data in region_values.items():
            target = results.setdefault(
                region,
                {},
            )

            for year, value in region_data.items():
                target.setdefault(
                    int(year),
                    value,
                )

    if not results:
        raise RuntimeError(
            "Tidak ada data AKI yang berhasil diekstrak "
            "dari sumber yang diberikan."
        )

    return results


def _get_all_data(source_url, requested_year=None):
    cache_key = str(source_url or "").strip()

    if not cache_key:
        raise RuntimeError("URL sumber AKI kosong.")

    with _AKI_LOCK:
        if cache_key in _ALL_DATA_CACHE:
            return _ALL_DATA_CACHE[cache_key]

        data = _build_all_data(
            cache_key,
            requested_year=requested_year,
        )

        _ALL_DATA_CACHE[cache_key] = data

        print(
            f"    [AKI AUTO] "
            f"{len(data)} wilayah tersedia."
        )

        return data


def _get_all_data_with_fallback(source_url, requested_year=None):
    """
    Coba sumber dari Master terlebih dahulu. Bila gagal (mis. HTTP 403
    karena situs memblokir scraper), lanjut ke FALLBACK_SOURCES.
    """
    from sources.source_resolver import is_http_url, mark_default_used

    candidates = []
    primary = str(source_url or "").strip()

    if primary and primary not in FALLBACK_SOURCES:
        if is_http_url(primary):
            candidates.append(primary)
        else:
            # Sumber_1 bukan URL yang valid -> langsung pakai cadangan,
            # tandai transparan untuk laporan ExtractionResult.
            print(
                f"    [AKI AUTO] Sumber_1 bukan URL ('{primary[:60]}'), "
                f"pakai sumber cadangan: {FALLBACK_SOURCES[0]}"
            )
            mark_default_used(FALLBACK_SOURCES[0])

    for fallback in FALLBACK_SOURCES:
        if fallback not in candidates:
            candidates.append(fallback)

    errors = []

    for candidate in candidates:
        with _AKI_LOCK:
            if candidate in _FAILED_SOURCES:
                continue

        try:
            return _get_all_data(
                candidate,
                requested_year=requested_year,
            )

        except Exception as error:
            with _AKI_LOCK:
                _FAILED_SOURCES.add(candidate)
            errors.append(
                f"{candidate}: "
                f"{str(error).splitlines()[0][:220]}"
            )

            if candidate == primary and len(candidates) > 1:
                print(
                    "    [AKI AUTO] WARNING: Sumber dari Master gagal. "
                    f"{(str(error).splitlines()[0])[:220]}"
                )
                print(
                    "    [AKI AUTO] Mencoba sumber cadangan: "
                    f"{candidates[1]}"
                )

    raise RuntimeError(
        "Semua sumber AKI gagal. "
        + "; ".join(errors)
    )


def _resolve_targets(
    all_data,
    description,
    wilayah,
):
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

    canonical_names = list(
        all_data.keys()
    )

    kab_kota_names = [
        name
        for name in canonical_names
        if _normalize_region(name)
        != _normalize_region(
            PROVINSI_CANONICAL
        )
    ]

    if wants_kab_kota and wants_provinsi:
        return [
            PROVINSI_CANONICAL
        ] + kab_kota_names

    if wants_kab_kota:
        return kab_kota_names

    return [
        str(wilayah or "").strip()
        or PROVINSI_CANONICAL
    ]


def _value_for_region(
    all_data,
    target_region,
    tahun,
):
    target_norm = _normalize_region(
        target_region
    )

    for region, year_values in all_data.items():
        if _normalize_region(region) != target_norm:
            continue

        value = year_values.get(
            int(tahun)
        )

        if value is None:
            raise RuntimeError(
                f"Data AKI '{target_region}' "
                f"tahun {tahun} tidak tersedia."
            )

        return value

    raise RuntimeError(
        f"Wilayah '{target_region}' "
        "tidak ditemukan pada hasil parser AKI."
    )


def scrape_aki(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Adapter AKI untuk project Auto.

    URL sumber utama berasal dari Master/Form. Bila gagal (situs memblokir
    scraper), dipakai FALLBACK_SOURCES sebagai cadangan.
    """
    source_url = str(url or "").strip()

    if not source_url:
        raise RuntimeError(
            "URL sumber AKI kosong."
        )

    print(
        f"    [AKI AUTO] Sumber dari Master: {source_url}"
    )

    all_data = _get_all_data_with_fallback(
        source_url,
        requested_year=tahun,
    )

    targets = _resolve_targets(
        all_data,
        description,
        wilayah,
    )

    print(
        f"    [AKI AUTO] Tahun {tahun} | "
        f"target {len(targets)} wilayah."
    )

    values = {}
    errors = []

    for target in targets:
        try:
            values[target] = _value_for_region(
                all_data,
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
            "Tidak ada nilai AKI yang berhasil diekstrak untuk "
            f"tahun {tahun}. "
            + "; ".join(errors[:5])
        )

    if errors:
        print(
            f"    [AKI AUTO] WARNING: "
            f"{len(errors)} wilayah tidak tersedia."
        )

    if len(values) == 1:
        return next(
            iter(values.values())
        )

    return values
