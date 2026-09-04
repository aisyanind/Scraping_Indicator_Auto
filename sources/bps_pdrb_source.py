"""
Adapter BPS PDRB (Auto project).

Pengganti legacy `bps.py::scrape_bps_pdrb` yang berbasis Selenium:
kalsel.bps.go.id kini dilindungi Cloudflare dan memblokir Chrome
headless ("Performing security verification"), sehingga label PDRB
tidak pernah ditemukan di debug text.

Tabel statistik BPS yang baru menyematkan datanya sebagai JSON di
dalam HTML halaman, sehingga cukup diunduh sebagai teks:

- Query `?year=<tahun>` pada URL menentukan tahun tabel
  (dikonfirmasi lewat `tahun_data":<tahun>` di dalam halaman).
- Baris total "Produk Domestik Bruto" memuat `value_raw` berformat
  Indonesia, mis. "305.126,93" (miliar rupiah).
"""

import re
import urllib.parse
import urllib.request

import requests

from config import REQUEST_HEADERS

PAGE_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

# PDRB Kalsel ADHB dalam miliar rupiah: ~180 ribu (2019) s.d. ~300 ribu (2025).
PDRB_MIN = 50_000
PDRB_MAX = 1_000_000

_PDRB_CACHE = {}


def parse_number_id(value):
    """'305.126,93' -> 305126.93; '180.557' -> 180557.0."""
    if value is None:
        return None

    text = str(value).strip().replace(" ", "")

    if not text:
        return None

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        text = text.replace(".", "")

    try:
        return float(text)
    except ValueError:
        return None


def _with_year_param(url, tahun):
    """Ganti/tambah query ?year=<tahun> tanpa menyentuh path."""
    parsed = urllib.parse.urlsplit(str(url or ""))
    query = urllib.parse.parse_qs(parsed.query)
    query["year"] = [str(int(tahun))]

    new_query = urllib.parse.urlencode(
        {key: values[-1] for key, values in query.items()},
    )

    return urllib.parse.urlunsplit(
        parsed._replace(query=new_query),
    )


def _looks_like_cloudflare(text):
    lowered = str(text or "").lower()

    return (
        "security verification" in lowered
        or "just a moment" in lowered
        or "attention required" in lowered
    )


def _download_html(url, timeout=60):
    """
    Unduh halaman; validasi isinya benar-benar tabel (bukan challenge
    Cloudflare). requests dulu; jika SSL/blokir, fallback ke urllib
    (certificate store Windows, aman dari antivirus SSL scanning).
    """
    last_error = None

    for client in ("requests", "urllib"):
        try:
            if client == "requests":
                response = requests.get(
                    url,
                    headers=PAGE_REQUEST_HEADERS,
                    timeout=timeout,
                )
                response.raise_for_status()
                body = response.text
            else:
                request = urllib.request.Request(
                    url,
                    headers=dict(PAGE_REQUEST_HEADERS),
                )

                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read().decode("utf-8", "ignore")

            if _looks_like_cloudflare(body):
                raise RuntimeError(
                    "Halaman kalsel.bps.go.id terblokir Cloudflare "
                    "(security verification), bukan konten tabel."
                )

            if "produk domestik" not in body.lower():
                raise RuntimeError(
                    "Halaman tidak memuat konten PDRB yang diharapkan."
                )

            return body

        except Exception as error:
            last_error = error

            if client == "requests":
                print(
                    f"    [BPS-PDRB] requests gagal "
                    f"({error.__class__.__name__}); fallback ke urllib..."
                )

    raise RuntimeError(
        f"Gagal mengunduh halaman BPS ({str(last_error).splitlines()[0][:200]})"
    )


def _extract_value_near(segment):
    """Cari value_raw / value berformat Indonesia pada potongan JSON."""
    match = re.search(
        r"value_raw\D{0,30}(\d[\d., ]*\d|\d)",
        segment,
    )

    if match:
        return match.group(1)

    match = re.search(
        r"[\"']value[\"']\s*:\s*[\"']([\d.,<sup>/ ]+)[\"']",
        segment,
    )

    if match:
        return re.sub(r"<[^>]+>", "", match.group(1)).strip()

    return None


def _extract_pdrb_total(html, tahun):
    """
    Ambil baris total PDRB dari JSON yang tertanam di halaman.

    Return (nilai, label) atau (None, alasan).
    """
    lowered = html.lower()
    candidates = []

    for match in re.finditer(r"produk domestik", lowered):
        tail = lowered[match.start():match.start() + 80]

        if "bruto" not in tail:
            continue

        # Buang baris turunan: per kapita, distribusi, pertumbuhan.
        if re.search(
            r"per\s*kapita|distribusi|pertumbuhan|konstan",
            lowered[match.start():match.start() + 120],
        ):
            continue

        segment = html[match.start():match.start() + 900]
        raw = _extract_value_near(segment)

        if not raw:
            continue

        nilai = parse_number_id(raw)

        if nilai is None:
            continue

        candidates.append((nilai, raw))

    for nilai, raw in candidates:
        if PDRB_MIN <= nilai <= PDRB_MAX:
            return nilai, raw

    return None, (
        f"kandidat tidak masuk rentang wajar {PDRB_MIN}-{PDRB_MAX} "
        f"miliar Rp: {candidates}" if candidates else "label total tidak ditemukan"
    )


def _extract_table_year(html):
    match = re.search(
        r'tahun_data\\?"?\s*:\s*\\?"?(\d{4})',
        html,
    )

    return int(match.group(1)) if match else None


def _extract_available_years(html):
    match = re.search(
        r"list_tahun_data\\?\"\s*:\s*\\?\"\s*\[([^\]]*)\]",
        html,
    )

    if not match:
        return []

    return [
        int(year)
        for year in re.findall(r"\d{4}", match.group(1))
    ]


def scrape_bps_pdrb_page(url, tahun, description=None):
    """
    Return nilai PDRB ADHB (miliar rupiah) untuk satu tahun.

    URL Sumber boleh memuat ?year= apa pun -- parameter tahun pada
    URL diganti sesuai tahun yang diminta.
    """
    tahun = int(tahun)

    target_url = _with_year_param(url, tahun)

    cache_key = (target_url, tahun)

    if cache_key in _PDRB_CACHE:
        return _PDRB_CACHE[cache_key]

    print(
        f"    [BPS-PDRB] Mengunduh tabel PDRB tahun {tahun}: "
        f"{target_url[:110]}"
    )

    html = _download_html(target_url)

    table_year = _extract_table_year(html)

    if table_year is not None and table_year != tahun:
        available = _extract_available_years(html)

        raise RuntimeError(
            f"Tabel BPS yang diterisa untuk tahun {table_year}, "
            f"bukan {tahun}. Tahun tersedia: {available or 'tidak diketahui'}."
        )

    nilai, info = _extract_pdrb_total(html, tahun)

    if nilai is None:
        raise RuntimeError(
            f"Nilai total PDRB tahun {tahun} tidak ditemukan di halaman "
            f"BPS ({info})."
        )

    print(
        f"    [BPS-PDRB] PDRB {tahun} = {info} miliar Rp "
        f"(tahun_data={table_year})"
    )

    _PDRB_CACHE[cache_key] = float(nilai)

    return float(nilai)


__all__ = [
    "scrape_bps_pdrb_page",
]
