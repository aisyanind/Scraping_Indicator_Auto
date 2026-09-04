"""
Adapter INDEKS KETIMPANGAN GENDER (IKG) - Auto project.

Sumber: Berita Resmi Statistik (BRS) BPS Provinsi Kalimantan Selatan.

Kenapa adapter khusus (bukan generic HTML):
- Halaman listing BRS (`/id/pressrelease?keyword=ikg`) adalah Next.js SPA,
  daftar berita dimuat client-side via API. Generic extractor tidak bisa
  membaca tabel karena HTML statisnya kosong.
- Setiap tahun data IKG dipublikasikan pada berita yang berbeda, sehingga
  link artikel perlu ditemukan dari listing tiap kali.
- Data lengkap per kabupaten/kota + provinsi berada pada file PDF yang
  disematkan di halaman detail berita (Tabel 2).

Alur adapter:
1. Discovery link artikel BRS IKG dari listing (Selenium render JS).
2. Ambil `pdfUrl` (download.php) dari halaman detail tiap artikel.
3. Unduh PDF, parse Tabel 2 -> {wilayah: {tahun: nilai}}.
4. Gabungkan semua PDF, ambil nilai untuk tahun yang diminta.

Output mengikuti pola AKI: dict {wilayah: nilai} atau nilai tunggal.
"""

import logging
import re
import threading
import time
from io import BytesIO

import pdfplumber
import requests

logging.getLogger("pdfminer").setLevel(logging.ERROR)

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

PROVINSI_CANONICAL = "Prov. Kalimantan Selatan"

REGION_ALIASES = {
    "Kalimantan Selatan": PROVINSI_CANONICAL,
    "Tanah Laut": "Kab. Tanah Laut",
    "Kotabaru": "Kab. Kotabaru",
    "Banjar": "Kab. Banjar",
    "Barito Kuala": "Kab. Barito Kuala",
    "Tapin": "Kab. Tapin",
    "Hulu Sungai Selatan": "Kab. Hulu Sungai Selatan",
    "Hulu Sungai Tengah": "Kab. Hulu Sungai Tengah",
    "Hulu Sungai Utara": "Kab. Hulu Sungai Utara",
    "Tabalong": "Kab. Tabalong",
    "Tanah Bumbu": "Kab. Tanah Bumbu",
    "Balangan": "Kab. Balangan",
    "Kota Banjarmasin": "Kota Banjarmasin",
    "Kota Banjar Baru": "Kota Banjarbaru",
    "Kota Banjarbaru": "Kota Banjarbaru",
}

# Cache all_data per URL listing (dipakai antar-tahun dalam satu job).
_ALL_DATA_CACHE = {}
_IKG_LOCK = threading.Lock()


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
    name = str(source_name or "").strip()

    if name in REGION_ALIASES:
        return REGION_ALIASES[name]

    for alias, canonical in REGION_ALIASES.items():
        if _normalize_text(alias) == _normalize_text(name):
            return canonical

    return name


def _is_ikg_title(title):
    text = _normalize_text(title)

    # "IKG" di judul bisa berarti "Indeks Kesulitan Geografis" (Podes),
    # jadi tidak dipakai sebagai penanda; fokus pada kata ketimpangan.
    return (
        "ketimpangan gender" in text
        or re.search(r"(^|[^a-z])indeks ketimpangan([^a-z]|$)", text)
    )


def _is_ikg_url(href):
    slug = href.lower()

    return (
        "indeks-ketimpangan-gender" in slug
        or re.search(r"/pressrelease/.*indeks-ketimpangan-gender.*\.html", slug)
    )


def _discover_article_links(listing_url):
    """
    Buka halaman listing BRS (Next.js SPA) dengan Selenium, kumpulkan link
    artikel yang judulnya mengandung IKG / Indeks Ketimpangan Gender.

    Return list of {title, url}.
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 "
        "Safari/537.36"
    )

    articles = []
    driver = webdriver.Chrome(options=options)

    try:
        driver.set_page_load_timeout(90)

        # Halaman listing mungkin berpaginasi; scan beberapa halaman.
        for page in range(1, 6):
            separator = "&" if "?" in listing_url else "?"
            page_url = (
                f"{listing_url}{separator}page={page}"
            )

            print(f"    [IKG] Buka listing page={page}: {page_url}")

            driver.get(page_url)
            WebDriverWait(driver, 45).until(
                lambda d: d.execute_script(
                    "return document.readyState"
                ) == "complete"
            )
            time.sleep(6)

            links = driver.find_elements(By.TAG_NAME, "a")
            found = 0

            for anchor in links:
                href = anchor.get_attribute("href") or ""
                title = (
                    anchor.get_attribute("textContent") or ""
                ).strip()

                if "/pressrelease/" not in href:
                    continue

                if not (_is_ikg_title(title) or _is_ikg_url(href)):
                    continue

                if any(a["url"] == href for a in articles):
                    continue

                articles.append({
                    "title": title,
                    "url": href,
                })
                found += 1

            print(f"    [IKG] Page {page}: {found} artikel IKG baru.")

            if found == 0:
                break

    finally:
        driver.quit()

    if not articles:
        raise RuntimeError(
            "Tidak menemukan artikel BRS IKG pada listing: "
            f"{listing_url}"
        )

    return articles


def _extract_pdf_url(article_url):
    """
    Halaman detail BRS di-render server-side, berisi link download PDF
    (web-api.bps.go.id/download.php?f=...).
    """
    response = requests.get(
        article_url,
        headers=HEADERS,
        timeout=60,
    )
    response.raise_for_status()

    match = re.search(
        r'href="(https://web-api\.bps\.go\.id/download\.php\?f=[^"]+)"',
        response.text,
    )

    if not match:
        raise RuntimeError(
            "Tidak menemukan link PDF pada artikel BRS: "
            f"{article_url}"
        )

    return match.group(1)


def _download_pdf(pdf_url):
    response = requests.get(
        pdf_url,
        headers=HEADERS,
        timeout=120,
    )
    response.raise_for_status()

    if not response.content.startswith(b"%PDF"):
        raise RuntimeError(
            f"URL tidak mengembalikan PDF valid: {pdf_url}"
        )

    return BytesIO(response.content)


def _parse_ikg_table(pdf_stream):
    """
    Parse "Tabel 2 Perkembangan IKG Kabupaten/Kota" dari PDF BRS.

    Format teks (layout=False):
        Tabel 2 Perkembangan Indeks Ketimpangan Gender (IKG)
        Kabupaten/Kota, 2020–2025
        Provinsi 2020 2021 2022 2023 2024 2025
        (1) (3) (4) (5) (6) (7) (7)
        Tanah Laut 0,517 0,483 0,481 0,421 0,473 0,411
        ...
        Kalimantan Selatan 0,528 0,525 0,514 0,507 0,486 0,482

    Return {region_source: {tahun: nilai}}.
    """
    values = {}

    with pdfplumber.open(pdf_stream) as document:
        for page in document.pages:
            try:
                text = page.extract_text(layout=False) or ""
            except Exception as error:
                logging.warning(
                    "[IKG] Gagal membaca halaman PDF: %s",
                    error,
                )
                continue

            if "Tabel 2" not in text or "Provinsi" not in text:
                continue

            lines = [
                line.strip()
                for line in text.splitlines()
                if line.strip()
            ]

            header_years = []
            header_idx = None

            for index, line in enumerate(lines):
                if (
                    line.startswith("Provinsi")
                    and re.search(r"20\d{2}", line)
                ):
                    header_years = re.findall(
                        r"(20\d{2})",
                        line,
                    )
                    header_idx = index
                    break

            if not header_years:
                continue

            for line in lines[header_idx + 1:]:
                if re.fullmatch(
                    r"\(\d+\)(?:\s*\(\d+\))*",
                    line,
                ):
                    continue

                match = re.match(
                    r"^([A-Za-z][A-Za-z .]*?)\s+"
                    r"((?:\d+,\d+)(?:\s+\d+,\d+)*)$",
                    line,
                )

                if not match:
                    continue

                name = match.group(1).strip()
                raw_values = match.group(2).split()

                if len(raw_values) != len(header_years):
                    continue

                year_values = {}

                for year, raw in zip(header_years, raw_values):
                    year_values[int(year)] = float(
                        raw.replace(",", ".")
                    )

                values[name] = year_values

            break

    if not values:
        raise RuntimeError(
            "Tabel IKG kabupaten/kota tidak ditemukan di PDF."
        )

    return values


def _build_all_data(listing_url):
    articles = _discover_article_links(listing_url)

    print(
        f"    [IKG] {len(articles)} artikel BRS IKG ditemukan."
    )

    results = {}

    for article in articles:
        try:
            print(
                f"    [IKG] Proses artikel: {article['title'][:80]}"
            )

            pdf_url = _extract_pdf_url(article["url"])
            pdf_stream = _download_pdf(pdf_url)
            table = _parse_ikg_table(pdf_stream)

        except Exception as error:
            print(
                f"    [IKG] WARNING artikel gagal: "
                f"{str(error).splitlines()[0]}"
            )
            continue

        for source_region, year_values in table.items():
            canonical = _canonical_region(source_region)
            target = results.setdefault(canonical, {})

            for year, value in year_values.items():
                target.setdefault(int(year), value)

    if not results:
        raise RuntimeError(
            "Tidak ada data IKG yang berhasil diekstrak "
            "dari sumber yang diberikan."
        )

    return results


def _get_all_data(listing_url):
    cache_key = str(listing_url or "").strip()

    if not cache_key:
        raise RuntimeError("URL sumber IKG kosong.")

    with _IKG_LOCK:
        if cache_key in _ALL_DATA_CACHE:
            return _ALL_DATA_CACHE[cache_key]

        data = _build_all_data(cache_key)

        _ALL_DATA_CACHE[cache_key] = data

        print(
            f"    [IKG] {len(data)} wilayah tersedia."
        )

        return data


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
        != _normalize_region(PROVINSI_CANONICAL)
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
                f"Data IKG '{target_region}' "
                f"tahun {tahun} tidak tersedia."
            )

        return value

    raise RuntimeError(
        f"Wilayah '{target_region}' "
        "tidak ditemukan pada hasil parser IKG."
    )


def scrape_ikg(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Adapter IKG untuk project Auto.

    URL sumber berupa halaman listing BRS dengan keyword=ikg. Adapter
    menemukan sendiri link berita tiap tahun, mengunduh PDF-nya, lalu
    mengembalikan nilai IKG per wilayah sesuai deskripsi.
    """
    source_url = str(url or "").strip()

    if not source_url:
        raise RuntimeError("URL sumber IKG kosong.")

    print(
        f"    [IKG AUTO] Sumber dari Master: {source_url}"
    )

    all_data = _get_all_data(source_url)

    targets = _resolve_targets(
        all_data,
        description,
        wilayah,
    )

    print(
        f"    [IKG AUTO] Tahun {tahun} | "
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
            "Tidak ada nilai IKG yang berhasil diekstrak untuk "
            f"tahun {tahun}. "
            + "; ".join(errors[:5])
        )

    if errors:
        print(
            f"    [IKG AUTO] WARNING: "
            f"{len(errors)} wilayah tidak tersedia."
        )

    if len(values) == 1:
        return next(iter(values.values()))

    return values


__all__ = ["scrape_ikg"]
