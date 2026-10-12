import re
import urllib.parse
import urllib.request

import requests

from config import REQUEST_HEADERS

# =====================================================================
# Konstanta
# =====================================================================

PAGE_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

METRIC_SYNONYMS = {
    # Wisata / Transportasi
    "wisman": ["wisatawan", "mancanegara", "asing"],
    "wisatawan": ["wisman", "mancanegara", "asing"],
    "penumpang": ["pengunjung", "pelanggan", "penumpang", "penumpangi"],
    "kunjungan": ["kunjung", "datang", "kedatangan"],
    "angkutan": ["transportasi", "transport", "pengangkutan"],
    "udara": ["penerbangan", "bandara", "aviasi", "pesawat", "udara"],
    "laut": ["pelabuhan", "maritim", "bahari"],
    "darat": ["jalan", "rute", "lintas"],
    # Umum
    "jumlah": ["total", "volume", "akumulasi"],
    "nilai": ["amount", "value", "harga"],
    "persentase": ["persen", "prosentase", "%"],
    "laju": ["rate", "rasio"],
    "indeks": ["index", "idks"],
    "volume": ["kuantitas", "tonase"],
    "omzet": ["pendapatan", "revenue", "sales"],
    "luas": ["area", "surface"],
    "produksi": ["output", "hasil", "produksi", "outturn"],
    # Demografi / Kesehatan
    "penduduk": ["populasi", "warga", "masyarakat"],
    "angka kematian": ["mortalitas", "kematian", "death rate"],
    "angka kelahiran": ["natalitas", "birth rate"],
    "harapan hidup": ["life expectancy", "usia harapan"],
    "kesehatan": ["health", "klinik", "rumah sakit"],
    "gizi": ["nutrition", "malnutrition", "status gizi"],
    "stunting": ["stunting", "pendek", "gagal tumbuh"],
    "balita": ["toddler", "anak", "usia dini"],
    # Pendidikan
    "siswa": ["murid", "peserta didik", "student"],
    "sekolah": ["school", "institution"],
    "pendidikan": ["education", "sekolah", "siswa"],
    # Ekonomi / Keuangan
    "inflasi": ["inflation", "harga", "ihp", "indeks harga"],
    "pdrb": ["pdb", "gdp", "produk domestik bruto"],
    "konsumsi": ["consumption", "pemakaian", "penggunaan"],
    "investasi": ["investment", "penanaman modal"],
    "ekspor": ["export", "pengiriman"],
    "impor": ["import", "pengadaan"],
    "kemiskinan": ["poverty", "garis kemiskinan"],
    "pengangguran": ["unemployment", "tingkat pengangguran"],
    "industri": ["industry", "manufacturing", "manufaktur"],
    "jasa": ["service", "services", "sektor jasa"],
    "perdagangan": ["trade", "komersial", "dagang"],
    # Pertanian / NTP
    "petani": ["farmer", "tani", "ntp"],
    "tukar": ["exchange", "nilai tukar"],
    "ntp": ["nilai tukar petani", "tukar", "petani"],
    "komoditas": ["komoditi", "commodity", "hasil pertanian"],
    "harga": ["price", "price index", "indeks harga"],
    "pangan": ["food", "beras", "padi", "gandum"],
    "tanaman": ["crop", "plants", "tanaman pangan"],
    "ternak": ["livestock", "ternak", "hewan"],
    "perkebunan": ["plantation", "kebun", "estate"],
    "pertanian": ["agriculture", "agrikultur", "farming"],
    # Lingkungan
    "curah hujan": ["rainfall", "precipitation"],
    "suhu": ["temperature", "temperatur"],
    "energi": ["energy", "listrik", "tenaga"],
    "air": ["water", "air bersih", "sanitasi"],
    "sampah": ["waste", "refuse", "limbah"],
    "emisi": ["emission", "co2", "gas rumah kaca"],
    "energi terbarukan": ["renewable", "solar", "angin", "hydro"],
}

RENTANG_WAJAR = {
    "default": (0, 10_000_000),
    "indeks": (0, 200),
    "persentase": (0, 100),
    "jumlah penduduk": (1_000, 10_000_000),
    "pdrb": (1_000, 100_000_000),
    "produksi": (0, 10_000_000),
    "luas": (0, 10_000_000),
}

# =====================================================================
# Parsing Angka
# =====================================================================

def parse_number_id(value):
    """
    Parse angka format Indonesia.

    '305.126,93' -> 305126.93
    '180.557' -> 180557.0
    '128.352' -> 128352.0
    '105,32' -> 105.32
    '1.05' -> 1.05 (desimal, bukan ribuan)
    """
    if value is None:
        return None

    text = str(value).strip().replace(" ", "")

    if not text:
        return None

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif "." in text:
        parts = text.split(".")
        if len(parts) > 1 and len(parts[-1]) == 3:
            text = text.replace(".", "")

    try:
        return float(text)
    except ValueError:
        return None


# =====================================================================
# HTTP Download
# =====================================================================

def looks_like_cloudflare(text):
    """Deteksi halaman Cloudflare challenge."""
    lowered = str(text or "").lower()
    return (
        "security verification" in lowered
        or "just a moment" in lowered
        or "attention required" in lowered
    )


def download_html(url, timeout=60, tag="BPS", validate=None):
    """
    Unduh halaman BPS; validasi bukan Cloudflare challenge.
    requests dulu; jika gagal, fallback ke urllib.

    tag: label untuk log (misal "BPS-UDARA", "BPS-NTP").
    validate: optional callable(body) -> None. Raise RuntimeError jika gagal.
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

            if looks_like_cloudflare(body):
                raise RuntimeError(
                    "Halaman BPS terblokir Cloudflare."
                )

            if validate:
                validate(body)

            return body

        except Exception as error:
            last_error = error
            if client == "requests":
                print(
                    f"    [{tag}] requests gagal "
                    f"({error.__class__.__name__}); fallback ke urllib..."
                )

    raise RuntimeError(
        f"Gagal mengunduh halaman BPS "
        f"({str(last_error).splitlines()[0][:200]})"
    )


def download_pdf(url, timeout=120):
    """Download PDF dari BPS. Return bytes."""
    for client in ("requests", "urllib"):
        try:
            if client == "requests":
                response = requests.get(
                    url,
                    headers=PAGE_REQUEST_HEADERS,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.content
            else:
                request = urllib.request.Request(
                    url,
                    headers=dict(PAGE_REQUEST_HEADERS),
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read()

        except Exception:
            continue

    raise RuntimeError(f"Gagal mengunduh PDF dari BPS: {url[:100]}")


def download_file(url, timeout=120):
    """Download file dari BPS. Return (bytes, content_type)."""
    for client in ("requests", "urllib"):
        try:
            if client == "requests":
                response = requests.get(
                    url,
                    headers=PAGE_REQUEST_HEADERS,
                    timeout=timeout,
                )
                response.raise_for_status()
                return response.content, response.headers.get("Content-Type", "")
            else:
                request = urllib.request.Request(
                    url,
                    headers=dict(PAGE_REQUEST_HEADERS),
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    content_type = response.headers.get("Content-Type", "")
                    return response.read(), content_type

        except Exception:
            continue

    raise RuntimeError(f"Gagal mengunduh file dari BPS: {url[:100]}")


# =====================================================================
# URL Utilities
# =====================================================================

def with_year_param(url, tahun):
    """Ganti/tambah query ?year=<tahun> tanpa menyentuh path."""
    parsed = urllib.parse.urlsplit(str(url or ""))
    query = urllib.parse.parse_qs(parsed.query)
    query["year"] = [str(int(tahun))]
    new_query = urllib.parse.urlencode(
        {key: values[-1] for key, values in query.items()}
    )
    return urllib.parse.urlunsplit(parsed._replace(query=new_query))


def is_press_release(url):
    """Deteksi apakah URL adalah halaman press release BPS."""
    path = urllib.parse.urlparse(str(url or "")).path.lower()
    return "/pressrelease/" in path


def find_pdf_url(html):
    """Extract URL PDF download dari halaman BPS."""
    # Pattern 1: BPS download API
    match = re.search(
        r'href=["\']([^"\']*web-api\.bps\.go\.id/download\.php[^"\']*)["\']',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()

    # Pattern 2: BPS download API (bare URL)
    match = re.search(
        r'(https?://web-api\.bps\.go\.id/download\.php\?f=[A-Za-z0-9_/+=-]+)',
        html,
    )
    if match:
        return match.group(1).strip()

    # Pattern 3: Link .pdf biasa (href attribute)
    match = re.search(
        r'href=["\']([^"\']*\.pdf)["\']',
        html,
        re.IGNORECASE,
    )
    if match:
        pdf_url = match.group(1).strip()
        if pdf_url.startswith(("http://", "https://")):
            return pdf_url
        return pdf_url

    # Pattern 4: URL .pdf bare (bukan di href)
    match = re.search(
        r'(https?://[^\s"\'<>]+\.pdf)',
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()

    return None


# =====================================================================
# JSON / HTML Extraction
# =====================================================================

def extract_value_near(segment):
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


def extract_table_year(html):
    """Ekstrak tahun_data dari halaman BPS."""
    match = re.search(
        r'tahun_data\\?"?\s*:\s*\\?"?(\d{4})',
        html,
    )
    return int(match.group(1)) if match else None


def extract_available_years(html):
    """Ekstrak list_tahun_data dari halaman BPS."""
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


GENERIC_STOPWORDS = {
    "kode", "industri", "pengolahan", "skala", "mikro", "kecil",
    "nilai", "data", "tahun", "angka", "jumlah", "total", "nama",
    "provinsi", "kabupaten", "kota", "wilayah", "daerah", "periode",
    "bulan", "kuarter", "semester", "deskripsi", "keterangan", "satuan",
    "sumber", "indikator", "komoditas", "jenis", "klasifikasi",
}


def _is_valid_metric_term(term):
    """Validasi apakah term adalah nama metrik yang valid (bukan kata umum)."""
    if len(term) < 3:
        return False
    if term in GENERIC_STOPWORDS:
        return False
    if term in METRIC_SYNONYMS:
        return True
    for key, synonyms in METRIC_SYNONYMS.items():
        if term in synonyms:
            return True
    return False


def extract_metric_terms(description):
    """Ekstrak metric terms dari description/nama indikator."""
    lower = str(description or "").lower()
    terms = []

    for metric, synonyms in METRIC_SYNONYMS.items():
        for syn in [metric] + synonyms:
            if syn in lower and metric not in terms:
                terms.append(metric)
                break

    if not terms:
        tokens = re.findall(r"[a-zA-Z]{3,}", lower)
        stop = {"untuk", "tahun", "di", "dan", "dari", "yang", "seluruh", "semua"}
        terms = [t for t in tokens if t not in stop][:3]

    return terms


def extract_valid_metric_terms(description):
    """Ekstrak metric terms yang valid dari description (ketat, tanpa kata umum)."""
    lower = str(description or "").lower()
    terms = []

    for metric, synonyms in METRIC_SYNONYMS.items():
        for syn in [metric] + synonyms:
            if syn in lower and metric not in terms:
                if _is_valid_metric_term(metric):
                    terms.append(metric)
                break

    if not terms:
        tokens = re.findall(r"[a-zA-Z]{4,}", lower)
        terms = [t for t in tokens if _is_valid_metric_term(t)][:3]

    return terms[:3]


def detect_rentang(metric_terms):
    """Detect rentang wajar berdasarkan metric terms."""
    for term in metric_terms:
        if term in RENTANG_WAJAR:
            return RENTANG_WAJAR[term]

    if any(t in metric_terms for t in ("indeks", "ntp", "inflasi")):
        return RENTANG_WAJAR["indeks"]

    return RENTANG_WAJAR["default"]
