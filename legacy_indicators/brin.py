import re
import xml.etree.ElementTree as ET
from io import BytesIO
from urllib.parse import urljoin

import pdfplumber
import requests

from utils import REQUEST_HEADERS, SCRAPE_CACHE, format_id


# ============================================================
# BRIN SOURCE HUB
#
# Satu file untuk semua indikator yang bersumber dari BRIN.
#
# Tugas file ini:
# - mencari publikasi resmi BRIN
# - mencari / download PDF
# - cache file
# - memilih parser indikator BRIN
# - parsing IDSD
#
# Tidak membaca Google Sheet.
# Tidak mengirim ke Google Sheet.
# Tidak menyimpan Apps Script URL / token.
#
# main.py cukup memanggil:
#
#     scrape_brin_source(
#         url=url,
#         tahun=tahun,
#         description=description,
#         wilayah=wilayah,
#     )
#
# Jika nanti ada indikator BRIN baru:
# tambahkan parser + handler-nya di file ini.
# ============================================================


# ============================================================
# KONFIGURASI UMUM BRIN
# ============================================================

BRIN_RSS_FEEDS = [
    "https://penerbit.brin.go.id/others/gateway/plugin/WebFeedGatewayPlugin/rss2",
    "https://penerbit.brin.go.id/press/gateway/plugin/WebFeedGatewayPlugin/rss2",
]

KNOWN_PUBLICATIONS = {
    ("indeks daya saing daerah", 2022):
        "https://penerbit.brin.go.id/press/catalog/book/750",

    ("indeks daya saing daerah", 2023):
        "https://penerbit.brin.go.id/others/catalog/book/1036",

    ("indeks daya saing daerah", 2024):
        "https://penerbit.brin.go.id/others/catalog/book/1673",

    ("indeks daya saing daerah", 2025):
        "https://penerbit.brin.go.id/others/catalog/book/2438",
}

DEFAULT_WILAYAH = "Prov. Kalimantan Selatan"

KALSEL_PROVINSI = [
    "Prov. Kalimantan Selatan",
]

KALSEL_KAB_KOTA = [
    "Kab. Tanah Laut",
    "Kab. Kotabaru",
    "Kab. Banjar",
    "Kab. Barito Kuala",
    "Kab. Tapin",
    "Kab. Hulu Sungai Selatan",
    "Kab. Hulu Sungai Tengah",
    "Kab. Hulu Sungai Utara",
    "Kab. Tabalong",
    "Kab. Tanah Bumbu",
    "Kab. Balangan",
    "Kota Banjarmasin",
    "Kota Banjarbaru",
]

KALSEL_ALL_WILAYAHS = (
    KALSEL_PROVINSI + KALSEL_KAB_KOTA
)

IDSD_PUBLICATION_TITLE = "Indeks Daya Saing Daerah"

# Seri BRIN yang parser ini gunakan dimulai 2022.
MIN_BRIN_IDSD_YEAR = 2022


# ============================================================
# HELPER UMUM
# ============================================================

def _normalize_text(value):
    text = str(value or "")
    text = (
        text
        .replace("\u00a0", " ")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip().lower()


def _normalize_text_upper(value):
    return _normalize_text(value).upper()


def _normalize_wilayah(wilayah):
    text = str(
        wilayah or DEFAULT_WILAYAH
    ).strip()

    if (
        not text
        or text.lower() in {"nan", "none", "-"}
    ):
        return DEFAULT_WILAYAH

    return text


def _request_get(url, timeout=60):
    last_error = None

    for attempt in range(1, 4):
        try:
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
        ) as error:
            last_error = error

            print(
                f"    [BRIN] Request gagal "
                f"({attempt}/3): {error}"
            )

    raise RuntimeError(
        "Gagal mengakses BRIN setelah 3 percobaan. "
        f"Error terakhir: {last_error}"
    )


# ============================================================
# CARI PUBLIKASI BRIN
# ============================================================

def find_brin_publication_page(title, tahun):
    title_normalized = _normalize_text(title)
    tahun = int(tahun)

    cache_key = (
        "brin_publication_page",
        title_normalized,
        tahun,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    known_url = KNOWN_PUBLICATIONS.get(
        (
            title_normalized,
            tahun,
        )
    )

    if known_url:
        SCRAPE_CACHE[cache_key] = known_url
        return known_url

    print(
        f"    [BRIN] Mencari publikasi "
        f"'{title} {tahun}'..."
    )

    for feed_url in BRIN_RSS_FEEDS:
        try:
            response = _request_get(
                feed_url,
                timeout=60,
            )

            root = ET.fromstring(
                response.content
            )

        except Exception as error:
            print(
                f"    [BRIN] RSS gagal dibaca: {error}"
            )
            continue

        for item in root.findall(".//item"):
            item_title = _normalize_text(
                item.findtext("title")
            )

            item_link = str(
                item.findtext("link")
                or ""
            ).strip()

            if not item_link:
                continue

            if (
                title_normalized in item_title
                and str(tahun) in item_title
            ):
                SCRAPE_CACHE[
                    cache_key
                ] = item_link

                return item_link

    raise RuntimeError(
        "SOURCE_UNAVAILABLE: "
        f"Publikasi BRIN '{title} {tahun}' "
        "tidak ditemukan pada katalog resmi BRIN."
    )


# ============================================================
# CARI PDF DARI KATALOG BRIN
# ============================================================

def _extract_hrefs(html_text, base_url):
    hrefs = re.findall(
        r'href\s*=\s*["\']([^"\']+)["\']',
        html_text,
        flags=re.IGNORECASE,
    )

    result = []

    for href in hrefs:
        absolute = urljoin(
            base_url,
            href.strip(),
        )

        if absolute not in result:
            result.append(absolute)

    return result


def find_pdf_url_from_catalog(catalog_url):
    cache_key = (
        "brin_pdf_url",
        catalog_url,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    print(
        f"    [BRIN] Membuka katalog: "
        f"{catalog_url}"
    )

    response = _request_get(
        catalog_url,
        timeout=60,
    )

    hrefs = _extract_hrefs(
        response.text,
        response.url,
    )

    direct_downloads = [
        url
        for url in hrefs
        if "/catalog/download/" in url
    ]

    view_pages = [
        url
        for url in hrefs
        if "/catalog/view/" in url
    ]

    # --------------------------------------------------------
    # 1. Direct download
    # --------------------------------------------------------

    for candidate in direct_downloads:
        try:
            pdf_response = _request_get(
                candidate,
                timeout=120,
            )

            if (
                len(pdf_response.content) >= 4
                and pdf_response.content[:4] == b"%PDF"
            ):
                SCRAPE_CACHE[
                    cache_key
                ] = candidate

                SCRAPE_CACHE[
                    (
                        "brin_pdf_bytes_by_url",
                        candidate,
                    )
                ] = pdf_response.content

                return candidate

        except Exception:
            continue

    # --------------------------------------------------------
    # 2. Halaman view -> cari download
    # --------------------------------------------------------

    for candidate in view_pages:
        try:
            view_response = _request_get(
                candidate,
                timeout=60,
            )

            view_hrefs = _extract_hrefs(
                view_response.text,
                view_response.url,
            )

            for download_url in view_hrefs:
                if "/catalog/download/" not in download_url:
                    continue

                pdf_response = _request_get(
                    download_url,
                    timeout=120,
                )

                if (
                    len(pdf_response.content) >= 4
                    and pdf_response.content[:4] == b"%PDF"
                ):
                    SCRAPE_CACHE[
                        cache_key
                    ] = download_url

                    SCRAPE_CACHE[
                        (
                            "brin_pdf_bytes_by_url",
                            download_url,
                        )
                    ] = pdf_response.content

                    return download_url

        except Exception:
            continue

    raise RuntimeError(
        "Tidak menemukan file PDF resmi pada "
        f"halaman katalog BRIN: {catalog_url}"
    )


def get_brin_publication_pdf(
    title,
    tahun,
):
    title_normalized = _normalize_text(
        title
    )

    tahun = int(tahun)

    cache_key = (
        "brin_publication_pdf",
        title_normalized,
        tahun,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    catalog_url = find_brin_publication_page(
        title=title,
        tahun=tahun,
    )

    pdf_url = find_pdf_url_from_catalog(
        catalog_url
    )

    content = SCRAPE_CACHE.get(
        (
            "brin_pdf_bytes_by_url",
            pdf_url,
        )
    )

    if content is None:
        print(
            f"    [BRIN] Mengunduh PDF resmi: "
            f"{pdf_url}"
        )

        response = _request_get(
            pdf_url,
            timeout=120,
        )

        content = response.content

    if (
        len(content) < 4
        or content[:4] != b"%PDF"
    ):
        raise RuntimeError(
            "File publikasi BRIN yang diterima "
            "bukan PDF valid."
        )

    SCRAPE_CACHE[
        cache_key
    ] = content

    return content


# ============================================================
# IDSD - HELPER ANGKA
# ============================================================

def _parse_idsd_number(value):
    if value is None:
        return None

    text = str(value).strip()

    if not text:
        return None

    matches = re.findall(
        r"(?<!\d)([0-5](?:[.,]\d{1,4})?)(?!\d)",
        text,
    )

    values = []

    for item in matches:
        try:
            number = float(
                item.replace(",", ".")
            )

            if 0 <= number <= 5:
                values.append(number)

        except ValueError:
            continue

    if not values:
        return None

    return values[-1]


# ============================================================
# IDSD - VARIAN WILAYAH
# ============================================================

def _wilayah_variants(wilayah):
    original = _normalize_text_upper(
        wilayah
    )

    variants = {
        original,
    }

    cleaned = original

    cleaned = re.sub(
        r"^PROV(?:INSI)?\.?\s+",
        "",
        cleaned,
    )

    cleaned = re.sub(
        r"^KAB(?:UPATEN)?\.?\s+",
        "",
        cleaned,
    )

    cleaned = re.sub(
        r"^KOTA\s+",
        "",
        cleaned,
    )

    cleaned = cleaned.strip()

    if cleaned:
        variants.add(cleaned)

    if original.startswith(
        ("KAB.", "KAB ", "KABUPATEN ")
    ):
        variants.add(
            "KAB " + cleaned
        )
        variants.add(
            "KABUPATEN " + cleaned
        )

    if original.startswith("KOTA "):
        variants.add(
            "KOTA " + cleaned
        )

    if original.startswith(
        ("PROV.", "PROV ", "PROVINSI ")
    ):
        variants.add(
            "PROVINSI " + cleaned
        )

    expanded = set()

    for variant in variants:
        expanded.add(variant)

        expanded.add(
            variant.replace(
                "BANJARBARU",
                "BANJAR BARU",
            )
        )

    return {
        item.strip()
        for item in expanded
        if item.strip()
    }


def _line_contains_target(
    line,
    variants,
):
    normalized_line = _normalize_text_upper(
        line
    )

    for variant in variants:
        if variant == "BANJAR":
            if (
                "BANJAR BARU" in normalized_line
                or "BANJARBARU" in normalized_line
            ):
                if "KAB BANJAR" not in normalized_line:
                    continue

        pattern = (
            r"(?<![A-Z])"
            + re.escape(variant)
            + r"(?![A-Z])"
        )

        if re.search(
            pattern,
            normalized_line,
        ):
            return True

    return False


# ============================================================
# IDSD - VALIDASI HALAMAN SKOR
# ============================================================

def _is_idsd_score_page(
    text,
    wilayah,
):
    normalized = _normalize_text_upper(
        text
    )

    if "IDSD" not in normalized:
        return False

    if "SKOR" not in normalized:
        return False

    wilayah_normalized = _normalize_text_upper(
        wilayah
    )

    is_kab_kota = bool(
        re.match(
            r"^(KAB\.?|KABUPATEN|KOTA)\b",
            wilayah_normalized,
        )
    )

    if is_kab_kota:
        return (
            "KABUPATEN" in normalized
            or "KOTA" in normalized
            or "KAB/KOTA" in normalized
        )

    return (
        "PROVINSI" in normalized
        or "PROV." in normalized
    )


# ============================================================
# IDSD - EKSTRAK DARI TEXT
# ============================================================

def _extract_idsd_from_layout_text(
    page,
    wilayah,
):
    text = (
        page.extract_text(
            x_tolerance=2,
            y_tolerance=2,
            layout=True,
        )
        or ""
    )

    if not _is_idsd_score_page(
        text,
        wilayah,
    ):
        return None

    variants = _wilayah_variants(
        wilayah
    )

    lines = [
        line.rstrip()
        for line in text.splitlines()
        if line.strip()
    ]

    for index, line in enumerate(lines):
        if not _line_contains_target(
            line,
            variants,
        ):
            continue

        value = _parse_idsd_number(
            line
        )

        if value is not None:
            return value

        neighbors = []

        if index > 0:
            neighbors.append(
                lines[index - 1]
            )

        if index + 1 < len(lines):
            neighbors.append(
                lines[index + 1]
            )

        for neighbor in neighbors:
            value = _parse_idsd_number(
                neighbor
            )

            if value is not None:
                return value

    return None


# ============================================================
# IDSD - EKSTRAK DARI TABEL
# ============================================================

def _extract_idsd_from_tables(
    page,
    wilayah,
):
    variants = _wilayah_variants(
        wilayah
    )

    tables = (
        page.extract_tables()
        or []
    )

    for table in tables:
        if not table:
            continue

        for row in table:
            if not row:
                continue

            cells = [
                str(cell or "").strip()
                for cell in row
            ]

            joined = " | ".join(
                cells
            )

            if not _line_contains_target(
                joined,
                variants,
            ):
                continue

            for cell in reversed(cells):
                value = _parse_idsd_number(
                    cell
                )

                if value is not None:
                    return value

    return None


# ============================================================
# IDSD - EKSTRAK DARI REGEX HALAMAN
# ============================================================

def _extract_idsd_from_page_regex(
    page,
    wilayah,
):
    text = (
        page.extract_text()
        or ""
    )

    normalized = _normalize_text_upper(
        text
    )

    variants = _wilayah_variants(
        wilayah
    )

    for variant in sorted(
        variants,
        key=len,
        reverse=True,
    ):
        after_pattern = (
            re.escape(variant)
            +
            r".{0,80}?"
            r"(?<!\d)"
            r"([0-5][.,]\d{1,4})"
            r"(?!\d)"
        )

        match = re.search(
            after_pattern,
            normalized,
            flags=re.DOTALL,
        )

        if match:
            return float(
                match.group(1)
                .replace(",", ".")
            )

        before_pattern = (
            r"(?<!\d)"
            r"([0-5][.,]\d{1,4})"
            r"(?!\d)"
            r".{0,80}?"
            +
            re.escape(variant)
        )

        match = re.search(
            before_pattern,
            normalized,
            flags=re.DOTALL,
        )

        if match:
            return float(
                match.group(1)
                .replace(",", ".")
            )

    return None


# ============================================================
# IDSD - EKSTRAK DARI WORD COORDINATES
# ============================================================

def _extract_idsd_from_row_context(
    page,
    wilayah,
):
    """
    Fallback untuk halaman skor IDSD yang nama wilayahnya terpecah
    lintas baris, misalnya:

        HULU SUNGAI          <- bagian 1 nama
        4,75 ... 2,95        <- baris angka (IDSD = 2,95)
        SELATAN              <- bagian 2 nama

    Strategi:
    1. Ambil kata-kata dengan koordinat (x0, top) via extract_words().
    2. Kelompokkan per baris berdasarkan top.
    3. Identifikasi baris data = baris dengan >= 10 angka desimal 0-5
       (12 pilar + 1 IDSD).
    4. IDSD score = angka dengan x0 terbesar (kolom paling kanan).
    5. Nama wilayah = gabungan kata NON-angka dari:
       a. baris data itu sendiri (kolom kiri x0 < 130),
       b. baris non-data terdekat di atas,
       c. baris non-data terdekat di bawah.
    6. Cocokkan dengan variants.
    """
    variants = _wilayah_variants(wilayah)

    try:
        words = page.extract_words(
            use_text_flow=False,
            keep_blank_chars=False,
        )
    except Exception:
        return None

    if not words:
        return None

    # Kelompokkan kata per baris berdasarkan top.
    rows_map = {}

    for w in words:
        key = round(float(w.get("top", 0)), 1)
        rows_map.setdefault(key, []).append(w)

    rows = [
        rows_map[key]
        for key in sorted(rows_map.keys())
    ]

    def _is_number(w):
        return _parse_idsd_number(w["text"]) is not None

    # Baris data: baris dengan >= 10 angka desimal.
    data_indexes = [
        idx
        for idx, row in enumerate(rows)
        if sum(1 for w in row if _is_number(w)) >= 10
    ]

    for idx in data_indexes:
        row = rows[idx]

        number_words = [
            w for w in row
            if _is_number(w)
        ]

        score_word = max(
            number_words,
            key=lambda w: w.get("x0", 0),
        )

        value = _parse_idsd_number(
            score_word["text"]
        )

        if value is None:
            continue

        # (a) Nama di baris data itu sendiri (x0 < 130 = kolom kiri).
        own_text = " ".join(
            w["text"]
            for w in row
            if not _is_number(w) and w.get("x0", 0) < 130
        )

        if _line_contains_target(own_text, variants):
            return value

        # (b) Baris non-data terdekat di atas.
        above_text = ""
        for j in range(idx - 1, -1, -1):
            if j in data_indexes:
                continue
            above_text = " ".join(
                w["text"]
                for w in rows[j]
                if not _is_number(w) and w.get("x0", 0) < 130
            )
            break

        # (c) Baris non-data terdekat di bawah.
        below_text = ""
        for j in range(idx + 1, len(rows)):
            if j in data_indexes:
                continue
            below_text = " ".join(
                w["text"]
                for w in rows[j]
                if not _is_number(w) and w.get("x0", 0) < 130
            )
            break

        combined = (
            (above_text + " " + own_text + " " + below_text)
            .strip()
        )

        if _line_contains_target(combined, variants):
            return value

    return None


# ============================================================
# IDSD - EXTRACT SCORE
# ============================================================

def extract_idsd_score(
    pdf_bytes,
    tahun,
    wilayah,
):
    tahun = int(tahun)
    wilayah = _normalize_wilayah(
        wilayah
    )

    with pdfplumber.open(
        BytesIO(pdf_bytes)
    ) as pdf:

        pages = list(
            pdf.pages
        )

        # ----------------------------------------------------
        # PASS 1: halaman yang jelas merupakan halaman skor IDSD.
        # ----------------------------------------------------

        for page_number, page in enumerate(
            pages,
            start=1,
        ):
            basic_text = (
                page.extract_text()
                or ""
            )

            if not _is_idsd_score_page(
                basic_text,
                wilayah,
            ):
                continue

            value = (
                _extract_idsd_from_layout_text(
                    page,
                    wilayah,
                )
            )

            if value is None:
                value = (
                    _extract_idsd_from_tables(
                        page,
                        wilayah,
                    )
                )

            if value is None:
                value = (
                    _extract_idsd_from_page_regex(
                        page,
                        wilayah,
                    )
                )

            if value is None:
                value = (
                    _extract_idsd_from_row_context(
                        page,
                        wilayah,
                    )
                )

            if value is not None:
                print(
                    f"    [IDSD] Skor ditemukan "
                    f"di halaman {page_number}."
                )

                return float(value)

        # ----------------------------------------------------
        # PASS 2: fallback seluruh halaman.
        # ----------------------------------------------------

        variants = _wilayah_variants(
            wilayah
        )

        for page_number, page in enumerate(
            pages,
            start=1,
        ):
            text = (
                page.extract_text()
                or ""
            )

            if not any(
                _line_contains_target(
                    text,
                    {variant},
                )
                for variant in variants
            ):
                continue

            value = (
                _extract_idsd_from_tables(
                    page,
                    wilayah,
                )
            )

            if value is None:
                value = (
                    _extract_idsd_from_page_regex(
                        page,
                        wilayah,
                    )
                )

            if value is None:
                value = (
                    _extract_idsd_from_row_context(
                        page,
                        wilayah,
                    )
                )

            if value is not None:
                print(
                    f"    [IDSD] Skor ditemukan "
                    f"di halaman {page_number} "
                    "(fallback)."
                )

                return float(value)

    raise RuntimeError(
        f"Skor IDSD untuk '{wilayah}' "
        f"tahun {tahun} tidak ditemukan "
        "pada PDF resmi BRIN."
    )


# ============================================================
# IDSD - SCRAPER
# ============================================================

def scrape_idsd(
    url,
    tahun,
    description,
    wilayah=None,
):
    tahun = int(tahun)

    wilayah = _normalize_wilayah(
        wilayah
    )

    cache_key = (
        "idsd",
        wilayah.lower(),
        tahun,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[
            cache_key
        ]

    print(
        f"    [IDSD] Mengambil IDSD "
        f"{wilayah} tahun {tahun}..."
    )

    if tahun < MIN_BRIN_IDSD_YEAR:
        raise RuntimeError(
            "SOURCE_UNAVAILABLE: "
            f"Parser IDSD BRIN ini menggunakan "
            f"seri {MIN_BRIN_IDSD_YEAR} ke atas. "
            f"Data tahun {tahun} tidak dipaksakan "
            "menggunakan metodologi 2022+."
        )

    pdf_bytes = get_brin_publication_pdf(
        title=IDSD_PUBLICATION_TITLE,
        tahun=tahun,
    )

    value = extract_idsd_score(
        pdf_bytes=pdf_bytes,
        tahun=tahun,
        wilayah=wilayah,
    )

    print(
        f"    [IDSD] "
        f"{wilayah} {tahun} = "
        f"{format_id(value)}"
    )

    SCRAPE_CACHE[
        cache_key
    ] = float(value)

    return float(value)



# ============================================================
# IDSD - CAKUPAN WILAYAH DARI KOLOM AMBIL_DATA
# ============================================================

def _resolve_idsd_targets(description, default_wilayah):
    text = _normalize_text(description)

    wants_kab_kota = any(
        marker in text
        for marker in (
            "kab/kota",
            "kabupaten/kota",
            "kabupaten dan kota",
            "kab kota",
            "kab./kota",
        )
    )

    wants_provinsi = any(
        marker in text
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
        result = []

        if wants_provinsi:
            result.extend(KALSEL_PROVINSI)

        result.extend(KALSEL_KAB_KOTA)
        return result

    return [_normalize_wilayah(default_wilayah)]


def scrape_idsd_requested_scope(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Single wilayah -> return float.
    Multi wilayah  -> return dict {Wilayah: skor}.
    """
    targets = _resolve_idsd_targets(
        description,
        wilayah,
    )

    if len(targets) == 1:
        return scrape_idsd(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=targets[0],
        )

    print(
        f"    [IDSD MULTI] Target = "
        f"{len(targets)} wilayah."
    )

    results = {}
    errors = {}

    for target in targets:
        try:
            results[target] = scrape_idsd(
                url=url,
                tahun=tahun,
                description=description,
                wilayah=target,
            )

        except Exception as error:
            errors[target] = (
                str(error)
                .split("\n")[0][:250]
            )

            print(
                f"    [IDSD MULTI] Tidak tersedia: "
                f"{target} | {errors[target]}"
            )

    if not results:
        examples = "; ".join(
            f"{region}: {message}"
            for region, message
            in list(errors.items())[:3]
        )

        raise RuntimeError(
            f"IDSD {tahun}: tidak ada wilayah "
            f"yang berhasil diekstrak. {examples}"
        )

    print(
        f"    [IDSD MULTI] Berhasil "
        f"{len(results)}/{len(targets)} wilayah."
    )

    return results



# ============================================================
# BRIN SOURCE ROUTER
# ============================================================

def scrape_brin_source(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Satu pintu untuk SEMUA indikator BRIN.

    Jika nanti ada indikator BRIN baru,
    tambahkan parser + kondisi baru di fungsi ini.
    main.py tidak perlu diubah.
    """

    url_text = _normalize_text(
        url
    )

    description_text = _normalize_text(
        description
    )

    combined = (
        url_text
        + " "
        + description_text
    )

    # ========================================================
    # IDSD
    # ========================================================

    if (
        "indeks-daya-saing-daerah" in combined
        or
        "indeks daya saing daerah" in combined
        or
        re.search(
            r"\bidsd\b",
            combined,
        )
    ):
        print(
            "  [BRIN HUB] Parser -> IDSD"
        )

        return scrape_idsd_requested_scope(
            url=url,
            tahun=tahun,
            description=description,
            wilayah=wilayah,
        )

    # ========================================================
    # INDIKATOR BRIN BERIKUTNYA DITAMBAHKAN DI SINI
    # ========================================================

    raise RuntimeError(
        "Parser BRIN belum tersedia untuk sumber ini. "
        f"URL={url}; description={description}"
    )


__all__ = [
    "scrape_brin_source",
    "scrape_idsd",
    "scrape_idsd_requested_scope",
    "extract_idsd_score",
    "find_brin_publication_page",
    "get_brin_publication_pdf",
]
