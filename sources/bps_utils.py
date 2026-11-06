import ipaddress
import re
import socket
import urllib.parse
import urllib.request

import requests
import urllib3
from urllib3.util import Timeout

from config import REQUEST_HEADERS

# =====================================================================
# Konstanta
# =====================================================================

PAGE_REQUEST_HEADERS = {
    **REQUEST_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
}

PDF_REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _is_bps_hostname(hostname):
    normalized = (hostname or "").lower().rstrip(".")
    return normalized == "bps.go.id" or normalized.endswith(".bps.go.id")


def _download_pdf_via_public_dns(url, timeout, referer=None):
    current_url = url
    for redirect_count in range(6):
        parsed = urllib.parse.urlsplit(current_url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or not _is_bps_hostname(hostname)
        ):
            raise RuntimeError(
                "fallback DNS publik hanya menerima URL HTTPS BPS"
            )

        dns_response = requests.get(
            "https://dns.google/resolve",
            params={"name": hostname, "type": "A"},
            headers={"Accept": "application/dns-json"},
            timeout=min(timeout, 10),
        )
        dns_response.raise_for_status()
        answer = dns_response.json()
        addresses = []
        for record in answer.get("Answer", []):
            if record.get("type") != 1:
                continue
            address = ipaddress.ip_address(record.get("data", ""))
            if address.is_global:
                addresses.append(str(address))
        if not addresses:
            raise RuntimeError(
                f"DNS publik tidak memberikan alamat IPv4 untuk {hostname}"
            )

        headers = {
            **PDF_REQUEST_HEADERS,
            "Host": parsed.netloc,
        }
        if referer:
            headers["Referer"] = referer

        request_path = parsed.path or "/"
        if parsed.query:
            request_path = f"{request_path}?{parsed.query}"

        errors = []
        redirected_url = None
        for address in dict.fromkeys(addresses):
            pool = urllib3.HTTPSConnectionPool(
                address,
                port=parsed.port or 443,
                server_hostname=hostname,
                assert_hostname=hostname,
                cert_reqs="CERT_REQUIRED",
                timeout=Timeout(
                    connect=min(timeout, 15),
                    read=timeout,
                ),
                retries=False,
            )
            try:
                response = pool.urlopen(
                    "GET",
                    request_path,
                    headers=headers,
                    redirect=False,
                )
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise RuntimeError(
                            f"HTTP {response.status} tanpa Location"
                        )
                    redirected_url = urllib.parse.urljoin(
                        current_url,
                        location,
                    )
                    redirected = urllib.parse.urlsplit(redirected_url)
                    if (
                        redirected.scheme != "https"
                        or not _is_bps_hostname(redirected.hostname)
                    ):
                        raise RuntimeError(
                            "redirect keluar dari domain HTTPS BPS ditolak"
                        )
                    break
                if response.status != 200:
                    body = response.data[:300].decode("utf-8", "replace")
                    raise RuntimeError(
                        f"HTTP {response.status}: {body}"
                    )
                if not response.data.startswith(b"%PDF"):
                    raise RuntimeError(
                        f"respons DNS publik bukan file PDF "
                        f"(HTTP {response.status})"
                    )
                return response.data
            except Exception as error:
                errors.append(
                    f"{address}: {error.__class__.__name__}: "
                    f"{str(error).splitlines()[0][:120]}"
                )
            finally:
                pool.close()

            if redirected_url:
                break

        if redirected_url:
            current_url = redirected_url
            continue
        raise RuntimeError("; ".join(errors))

    raise RuntimeError("terlalu banyak redirect saat mengunduh PDF BPS")


def _uses_private_dns(url):
    hostname = urllib.parse.urlsplit(url).hostname
    if not _is_bps_hostname(hostname):
        return False

    try:
        addresses = {
            ipaddress.ip_address(item[4][0])
            for item in socket.getaddrinfo(
                hostname,
                None,
                type=socket.SOCK_STREAM,
            )
        }
    except OSError:
        return False

    return bool(addresses) and any(
        not address.is_global for address in addresses
    )

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
    "pertumbuhan": ["growth", "laju", "kenaikan"],
    "ekonomi": ["economic", "economy", "pdrb"],
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
    "tbc": ["tuberkulosis", "tb"],
    "tuberkulosis": ["tbc", "tb"],
    "tb": ["tbc", "tuberkulosis"],
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


def download_pdf(url, timeout=120, referer=None):
    """Download PDF dari BPS, memakai header minimal yang kompatibel."""
    errors = []
    if _uses_private_dns(url):
        try:
            return _download_pdf_via_public_dns(
                url,
                timeout=timeout,
                referer=referer,
            )
        except Exception as error:
            errors.append(
                f"public DNS: {error.__class__.__name__}: "
                f"{str(error).splitlines()[0][:160]}"
            )

    header_options = [dict(PDF_REQUEST_HEADERS)]
    if referer:
        header_options.append({
            **PDF_REQUEST_HEADERS,
            "Referer": referer,
        })

    for header_index, headers in enumerate(header_options, start=1):
        for client in ("requests", "urllib"):
            try:
                if client == "requests":
                    response = requests.get(
                        url,
                        headers=headers,
                        timeout=timeout,
                    )
                    response.raise_for_status()
                    return response.content

                request = urllib.request.Request(
                    url,
                    headers=headers,
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.read()

            except Exception as error:
                errors.append(
                    f"{client}: {error.__class__.__name__}: "
                    f"{str(error).splitlines()[0][:120]}"
                )

    detail = "; ".join(errors)
    raise RuntimeError(f"Gagal mengunduh PDF dari BPS ({detail})")


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


def _contains_metric_phrase(text, phrase):
    """Cocokkan kata/frasa utuh agar sinonim tidak terdeteksi di dalam kata lain."""
    return bool(
        re.search(
            rf"(?<!\w){re.escape(str(phrase).strip())}(?!\w)",
            str(text or ""),
            re.IGNORECASE,
        )
    )


def extract_metric_terms(description):
    """Ekstrak metric terms dari description/nama indikator."""
    lower = str(description or "").lower()
    terms = []

    for metric, synonyms in METRIC_SYNONYMS.items():
        for syn in [metric] + synonyms:
            if _contains_metric_phrase(lower, syn) and metric not in terms:
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
            if _contains_metric_phrase(lower, syn) and metric not in terms:
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


# =====================================================================
# Satuan (unit) — deteksi & konversi
# =====================================================================

# Nama satuan kanonik -> faktor ke rupiah biasa.
UNIT_FACTORS = {
    "rupiah": 1,
    "ribu": 1_000,
    "juta": 1_000_000,
    "miliar": 1_000_000_000,
    "milyar": 1_000_000_000,
    "triliun": 1_000_000_000_000,
}

_UNIT_PATTERN = (
    r"(triliun|milyar|miliar|juta|ribu|rupiah|\brp\b)"
)


def normalize_unit(value):
    """
    Samakan istilah satuan ke bentuk kanonik.

    "Rp" / "rupiah" -> "rupiah", "thousand" -> "ribu",
    "million" -> "juta", "billion" -> "miliar".
    """
    text = str(value or "").strip().lower()

    aliases = {
        "rp": "rupiah",
        "rupiah": "rupiah",
        "thousand": "ribu",
        "ribu": "ribu",
        "ribuan": "ribu",
        "million": "juta",
        "juta": "juta",
        "billion": "miliar",
        "miliar": "miliar",
        "milyar": "miliar",
        "triliun": "triliun",
        "trillion": "triliun",
    }

    return aliases.get(text, text if text in UNIT_FACTORS else "")


def detect_unit_in_text(text):
    """
    Deteksi satuan dari teks bebas, mis. nama indikator
    "PDRB per kapita (Rp Juta)" -> "juta".

    "Rp <satuan>" diprioritaskan supaya "Rp Juta" terbaca "juta",
    bukan "rupiah". Return "" jika tidak ditemukan.
    """
    raw = str(text or "")

    # Prioritas: satuan yang mengikuti "Rp" (mis. "Rp Juta", "Rp Miliar").
    match = re.search(
        r"\brp\.?\s+(triliun|milyar|miliar|juta|ribuan|ribu)\b",
        raw,
        re.IGNORECASE,
    )
    if match:
        return normalize_unit(match.group(1))

    match = re.search(_UNIT_PATTERN, raw, re.IGNORECASE)
    if not match:
        return ""

    token = match.group(1).lower()

    if token == "rp":
        return "rupiah"

    return normalize_unit(token)


def detect_page_unit(html):
    """
    Deteksi satuan halaman BPS dari metadata (unit_multiplier_desc)
    atau dari judul tabel, mis. "(ribu rupiah)" -> "ribu".

    Return "" jika tidak ditemukan.
    """
    body = str(html or "")

    match = re.search(
        r"unit_multiplier_desc\\?\"\s*:\s*\\?\"([A-Za-z]+)",
        body,
    )
    if match:
        unit = normalize_unit(match.group(1))
        if unit:
            return unit

    match = re.search(
        r"\(([^)]*?)\s*(rupiah|rp)\)",
        body,
        re.IGNORECASE,
    )
    if match:
        unit = detect_unit_in_text(match.group(1))
        if unit and unit != "rupiah":
            return unit
        if unit == "rupiah" and "ribu" in match.group(1).lower():
            return "ribu"

    return ""


def convert_unit(value, from_unit, to_unit):
    """
    Konversi nilai antar satuan (ribu/juta/miliar/triliun/rupiah).

    - from_unit atau to_unit kosong/tidak dikenal -> nilai dikembalikan
      apa adanya (lebih baik tidak dikonversi daripada salah).
    - Konversi ke "rupiah" dianggap tujuan generik: faktor asal dipakai
      langsung tanpa pembagian (mis. ribu -> rupiah = x1000).

    Contoh: convert_unit(40773, "ribu", "juta") -> 40.773
    """
    if value is None:
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return value

    source = normalize_unit(from_unit)
    target = normalize_unit(to_unit)

    if not source or not target or source == target:
        return value

    source_factor = UNIT_FACTORS.get(source)
    target_factor = UNIT_FACTORS.get(target)

    if not source_factor or not target_factor:
        return value

    converted = number * source_factor / target_factor

    # Bulatkan maksimal 6 angka di belakang koma supaya tidak ada
    # artefak floating point (mis. 40.773000000000006).
    return round(converted, 6)
