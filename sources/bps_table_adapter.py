import json
import re
from io import StringIO
from urllib.parse import unquote, urljoin, urlsplit

import pandas as pd
import requests
from bs4 import BeautifulSoup

from sources.bps_utils import (
    PAGE_REQUEST_HEADERS,
    convert_unit,
    detect_page_unit,
    detect_rentang,
    detect_unit_in_text,
    download_html,
    extract_metric_terms,
    extract_table_year,
    normalize_unit,
    parse_number_id,
    with_year_param,
    METRIC_SYNONYMS,
)
from sources.region_utils import (
    KALSEL_KAB_KOTA,
    normalize_region,
    regions_match,
)

_CACHE = {}
_BPS_ACTION_CACHE = {}


def _is_bps_statistics_table_listing(url):
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").lower()
    path = parsed.path.rstrip("/").lower()
    return (
        (host == "bps.go.id" or host.endswith(".bps.go.id"))
        and path.endswith("/statistics-table")
    )


def _table_search_terms(description, nama_indikator=None):
    text = str(nama_indikator or description or "").lower()
    request_text = " ".join(
        part
        for part in (str(description or ""), str(nama_indikator or ""))
        if part
    )
    ignored = {
        "ambil", "angka", "data", "indikator", "nilai", "tahun",
        "untuk", "dengan", "pada", "dari", "yang", "dan", "di",
        "provinsi", "prov", "kabupaten", "kab", "kota", "seluruh",
        "semua",
    }
    if _is_poverty_rate_request(request_text):
        return ["persentase", "penduduk", "miskin"]

    return [
        term
        for term in dict.fromkeys(re.findall(r"[a-z0-9]+", text))
        if len(term) > 2 and term not in ignored
    ]


def _is_poverty_rate_request(text):
    normalized = str(text or "").lower()
    asks_poverty = any(
        term in normalized
        for term in ("kemiskinan", "miskin", "poverty")
    )
    asks_rate = (
        "%" in normalized
        or any(
            term in normalized
            for term in (
                "persentase",
                "persen",
                "tingkat",
                "poverty rate",
                "headcount",
            )
        )
    )
    asks_line = any(
        term in normalized
        for term in ("garis kemiskinan", "poverty line")
    )
    return asks_poverty and asks_rate and not asks_line


def _is_poverty_line_variable(name):
    normalized = str(name or "").lower()
    return "garis kemiskinan" in normalized or "poverty line" in normalized


def _is_poverty_rate_variable(name):
    normalized = str(name or "").lower()
    return any(
        marker in normalized
        for marker in ("persentase", "persen", "percentage", "%", "p0")
    )


def _is_poverty_count_variable(name):
    normalized = str(name or "").lower()
    return (
        "jumlah penduduk miskin" in normalized
        or "number of poor" in normalized
        or "poor population" in normalized
    )


def _table_title_score(title, terms):
    normalized = str(title or "").lower()
    return sum(term in normalized for term in terms)


def _metric_terms(description, nama_indikator=None):
    text = " ".join(
        part
        for part in (str(description or ""), str(nama_indikator or ""))
        if part
    )
    terms = extract_metric_terms(text)
    if _is_poverty_rate_request(text) and "persentase" not in terms:
        terms.insert(0, "persentase")
    if (
        ("%" in text or re.search(r"\bpersen(?:tase)?\b", text, re.I))
        and "persentase" not in terms
    ):
        terms.insert(0, "persentase")

    ignored = {
        "kode", "ambil", "data", "indikator", "tahun", "untuk",
        "dengan", "pada", "dari", "yang", "dan", "di", "seluruh",
        "semua", "prov", "provinsi", "kab", "kabupaten", "kota",
        "wilayah", "daerah", "terbaru",
    }
    terms = list(dict.fromkeys(
        term for term in terms if term not in ignored
    ))
    for token in re.findall(r"[a-z]{3,}", text.lower()):
        if token not in ignored and token not in terms:
            terms.append(token)

    return terms


def _within_metric_range(value, metric_terms):
    terms = set(metric_terms or [])
    if terms.intersection({
        "pertumbuhan", "laju", "growth", "rate", "inflasi",
    }):
        return -100 <= value <= 1_000
    minimum, maximum = detect_rentang(metric_terms)
    return minimum <= value <= maximum


def _find_catalog_detail_url(url, description, nama_indikator=None):
    html = download_html(url, tag="BPS-TABLE")
    soup = BeautifulSoup(html, "html.parser")
    terms = _table_search_terms(description, nama_indikator)
    candidates = []

    for anchor in soup.find_all("a", href=True):
        href = urljoin(url, str(anchor.get("href") or "").strip())
        path = urlsplit(href).path.lower()
        if not re.search(r"/statistics-table/[^/]+/[^/]+/", path):
            continue

        title = anchor.get_text(" ", strip=True)
        if not title:
            title = path.rsplit("/", 1)[-1].replace("-", " ")
        if (
            _is_poverty_rate_request(
                f"{description or ''} {nama_indikator or ''}"
            )
            and _is_poverty_line_variable(title)
        ):
            continue
        score = _table_title_score(title, terms)
        if terms and score == 0:
            continue
        candidates.append((score, title, href))

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = candidates[0][2] if candidates else None
    return selected, html, [f"catalog_rows_seen={len(candidates)}"]


# =====================================================================
# 1. Parser terstruktur halaman statistics-table BPS (jalur utama)
# =====================================================================

def _unescape_next_f(html):
    """
    Kumpulkan & unescape semua chunk self.__next_f.push([1,"..."]).

    Halaman BPS baru (App Router) menyimpan seluruh data tabel di
    dalam string escaped ini — bukan sebagai <table> biasa.
    """
    texts = []

    for match in re.finditer(
        r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
        str(html or ""),
    ):
        try:
            texts.append(json.loads('"' + match.group(1) + '"'))
        except (json.JSONDecodeError, ValueError):
            continue

    return "\n".join(texts)


def _find_json_after(text, key):
    """
    Cari `"<key>":` lalu decode JSON value-nya pakai raw_decode.

    Return (value, end_index) atau (None, None).
    """
    marker = f'"{key}":'
    idx = text.find(marker)

    if idx < 0:
        return None, None

    start = idx + len(marker)

    while start < len(text) and text[start] in " \t\r\n":
        start += 1

    if start >= len(text):
        return None, None

    try:
        value, end = json.JSONDecoder().raw_decode(text, start)
        return value, end
    except (json.JSONDecodeError, ValueError):
        return None, None


def _find_all_json_arrays(text, key):
    """
    Kumpulkan SEMUA array JSON untuk `"<key>": [` di seluruh teks.

    Halaman BPS memuat beberapa array "data" (metadata + baris tabel,
    kadang bersarang di dalam dict lain); pakai regex eksplisit `[`
    supaya array bersarang tetap ikut terdeteksi.
    """
    found = []

    for match in re.finditer(rf'"{re.escape(key)}"\s*:\s*\[', text):
        start = match.end() - 1  # posisi '['
        try:
            value, _ = json.JSONDecoder().raw_decode(text, start)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, list):
            found.append(value)

    return found


def _find_json_object(text, key):
    """
    Cari `"<key>": {` lalu decode object JSON-nya.

    Return dict (bisa kosong) atau {} jika tidak ditemukan.
    """
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\{{', text)
    if not match:
        return {}

    start = match.end() - 1  # posisi '{'
    try:
        value, _ = json.JSONDecoder().raw_decode(text, start)
    except (json.JSONDecodeError, ValueError):
        return {}

    return value if isinstance(value, dict) else {}


def _first_str_field(text, key):
    """Ambil nilai string sederhana `"key":"..."` dari payload."""
    match = re.search(
        f'"{key}":"((?:[^"\\\\]|\\\\.)*)"',
        text,
    )
    if not match:
        return None

    try:
        return json.loads('"' + match.group(1) + '"')
    except (json.JSONDecodeError, ValueError):
        return match.group(1)


def parse_bps_statistics_table(html):
    """
    Bongkar payload __next_f halaman /statistics-table/ BPS.

    Return dict:
      {
        "judul": str,            # judul_tabel
        "tahun_data": int|None,  # tahun data di halaman
        "list_tahun": [int],     # tahun yang tersedia
        "kolom": {id: {...meta, nama_variabel, unit_multiplier_desc...}},
        "rows": [
          {"label", "label_raw", "kode_wilayah",
           "variables": {id: {"value_raw", ...}}},
          ...
        ],
      }
    atau None jika struktur tidak dikenali.
    """
    text = _unescape_next_f(html)

    if not text:
        return None

    judul = _first_str_field(text, "judul_tabel") or ""

    tahun_data = None
    match = re.search(r'"tahun_data":\s*"?(\d{4})', text)
    if match:
        tahun_data = int(match.group(1))

    list_tahun = []
    value, _ = _find_json_after(text, "list_tahun_data")
    if isinstance(value, list):
        list_tahun = [int(y) for y in value if str(y).isdigit()]

    kolom = _find_json_object(text, "kolom")

    clean_rows = []
    for candidate in _find_all_json_arrays(text, "data"):
        if not isinstance(candidate, list):
            continue

        rows = []
        for row in candidate:
            if not isinstance(row, dict):
                continue
            if "variables" not in row and "kode_wilayah" not in row:
                continue
            rows.append(row)

        # Pilih array baris terpanjang (ada yang berisi data tabel
        # dan ada pula array metadata berukuran kecil).
        if len(rows) > len(clean_rows):
            clean_rows = rows

    if not clean_rows:
        return None

    return {
        "judul": judul,
        "tahun_data": tahun_data,
        "list_tahun": list_tahun,
        "kolom": kolom,
        "rows": clean_rows,
    }


def _score_column(meta, metric_terms, description):
    """
    Skor kecocokan nama kolom (nama_variabel) dengan metrik indikator.

    Bonus khusus: jika indikator menyebut "per kapita", kolom yang
    mengandung "per kapita" diunggulkan (dan sebaliknya) — ini yang
    membedakan PDRB total vs per kapita tanpa kode khusus.
    """
    name = str(meta.get("nama_variabel") or meta.get("nama_variabel_en") or "")
    name_lower = name.lower()

    score = 0.0

    for term in metric_terms:
        if len(term) < 3:
            continue
        if term in name_lower:
            score += 1.0
        else:
            synonyms = METRIC_SYNONYMS.get(term, [])
            if any(syn in name_lower for syn in synonyms):
                score += 0.7

    desc_lower = str(description or "").lower()
    wants_per_kapita = "per kapita" in desc_lower
    has_per_kapita = "per kapita" in name_lower

    if wants_per_kapita and has_per_kapita:
        score += 2.0
    elif wants_per_kapita and not has_per_kapita:
        score -= 2.0
    elif not wants_per_kapita and has_per_kapita:
        score -= 0.5

    return score


def _pick_column(table, metric_terms, description):
    """
    Pilih kolom (variabel) terbaik dari tabel.

    Return (col_id, meta) atau (None, None) jika tidak ada kolom.
    """
    kolom = table.get("kolom") or {}

    # Metadata kolom tidak ada — pakai id variabel dari baris pertama.
    if not kolom:
        for row in table.get("rows") or []:
            variables = row.get("variables") or {}
            if variables:
                col_id = next(iter(variables))
                return col_id, {}
        return None, None

    if len(kolom) == 1:
        col_id = next(iter(kolom))
        meta = kolom[col_id]
        meta = meta if isinstance(meta, dict) else {}
        name = meta.get("nama_variabel") or meta.get("nama_variabel_en")
        if _is_poverty_rate_request(description):
            if (
                _is_poverty_line_variable(name)
                or _is_poverty_count_variable(name)
                or not _is_poverty_rate_variable(name)
            ):
                return None, None
        return col_id, meta

    best_id = None
    best_meta = {}
    best_score = float("-inf")

    for col_id, meta in kolom.items():
        if not isinstance(meta, dict):
            meta = {}
        name = meta.get("nama_variabel") or meta.get("nama_variabel_en")
        if _is_poverty_rate_request(description):
            if (
                _is_poverty_line_variable(name)
                or _is_poverty_count_variable(name)
                or not _is_poverty_rate_variable(name)
            ):
                continue
        score = _score_column(meta, metric_terms, description)
        if score > best_score:
            best_id = col_id
            best_meta = meta
            best_score = score

    return best_id, best_meta


def _canonical_region(label, kode=None):
    """
    Samakan label baris tabel dengan nama wilayah kanonik sheet.

    Prioritas:
      1. cocokkan dengan daftar KALSEL_KAB_KOTA (label BPS tanpa
         prefix seperti "Kota Baru" -> "Kab. Kotabaru")
      2. prefix dari kode wilayah (6371xxx -> "Kota ...",
         63xxxxx -> "Prov. ..." jika kode provinsi)
      3. label apa adanya
    """
    text = str(label or "").strip()
    if not text:
        return text

    for known in KALSEL_KAB_KOTA:
        if regions_match(text, known):
            return known

    if isinstance(kode, int):
        # Kode provinsi: 6300000, 3100000, dst. (akhir 000000)
        if kode > 0 and kode % 1_000_000 == 0:
            bare = re.sub(
                r"^(prov\.|kab\.|kota|provinsi|kabupaten)\s+",
                "",
                text,
                flags=re.IGNORECASE,
            )
            return f"Prov. {bare}"

        # Kode kota BPS: dua digit ke-3/4 >= 71 (mis. 6371, 3271).
        pair = (kode // 1000) % 100
        if pair >= 71:
            bare = re.sub(
                r"^(kota|kab\.|kabupaten)\s+",
                "",
                text,
                flags=re.IGNORECASE,
            )
            return f"Kota {bare}"

        bare = re.sub(
            r"^(kab\.|kabupaten|kota)\s+",
            "",
            text,
            flags=re.IGNORECASE,
        )
        return f"Kab. {bare}"

    return text


def _row_value(row, col_id):
    """Ambil value_raw baris untuk kolom tertentu; None jika kosong."""
    variables = row.get("variables") or {}

    if not col_id or col_id not in variables:
        return None

    cell = variables[col_id]

    if not isinstance(cell, dict):
        return None

    raw = cell.get("value_raw")
    if raw is None:
        raw = cell.get("value")

    return parse_number_id(raw)


def _match_rows(table, col_id, targets):
    """
    Cocokkan baris tabel dengan daftar target wilayah.

    - targets terisi: hanya baris yang match target yang diambil
      (jangan menyeret baris lain — penting untuk tabel nasional).
    - targets kosong/None: ambil semua baris valid dengan nama
      kanonik dari label+kode (dipakai fallback anak-wilayah).

    Return dict {region_kanonik: nilai_mentah}.
    """
    matched = {}
    rows = table.get("rows") or []

    for row in rows:
        nilai = _row_value(row, col_id)
        if nilai is None:
            continue

        label = row.get("label") or row.get("label_raw") or ""
        kode = row.get("kode_wilayah")

        if targets:
            # cocokkan dengan target wilayah indikator
            hit_target = None
            for target in targets:
                if regions_match(label, target):
                    hit_target = target
                    break

            if hit_target is not None:
                matched[hit_target] = nilai
            # baris di luar target: TIDAK diambil

        else:
            # tanpa target: semua baris, nama kanonik dari label+kode
            region = _canonical_region(label, kode)
            if region:
                matched.setdefault(region, nilai)

    return matched


def _provincial_title_target(table, targets):
    if not _target_is_province(targets):
        return None

    rows = table.get("rows") or []
    if any(row.get("kode_wilayah") is not None for row in rows):
        return None

    title = str(table.get("judul") or "")
    title_region = re.search(
        r"\bdi\s+(prov(?:insi)?\.?\s+[^,(]+)",
        title,
        flags=re.IGNORECASE,
    )
    if not title_region:
        return None

    region_label = re.sub(
        r"\s+\d{4}\s*$",
        "",
        title_region.group(1),
    ).strip()
    return next(
        (
            candidate
            for candidate in targets
            if regions_match(region_label, candidate)
        ),
        None,
    )


def _match_provincial_sector_row(
    table,
    col_id,
    targets,
    metric_terms,
):
    """Match an explicitly requested industry row in a province-specific table."""
    target = _provincial_title_target(table, targets)
    if target is None:
        return {}

    if not {"industri", "pengolahan"}.issubset(set(metric_terms or [])):
        return {}

    for row in table.get("rows") or []:
        label = BeautifulSoup(
            str(row.get("label") or row.get("label_raw") or ""),
            "html.parser",
        ).get_text(" ", strip=True)
        label = re.sub(r"^[A-Z]\s+", "", label.strip())
        label = re.split(r"\s*/\s*", label, maxsplit=1)[0]
        if re.sub(r"\s+", " ", label).strip().casefold() != (
            "industri pengolahan"
        ):
            continue

        value = _row_value(row, col_id)
        if value is not None:
            return {target: value}

    return {}


def _match_provincial_aggregate_row(
    table,
    col_id,
    targets,
    metric_terms=None,
):
    """Match the total PDRB row, not a sector-specific indicator."""
    if {"industri", "pengolahan"}.issubset(set(metric_terms or [])):
        return {}

    target = _provincial_title_target(table, targets)
    if target is None:
        return {}

    rows = table.get("rows") or []
    aggregate_labels = {
        "produk domestik regional bruto",
        "produk domestik bruto/produk domestik regional bruto",
    }
    for row in rows:
        label = BeautifulSoup(
            str(row.get("label") or row.get("label_raw") or ""),
            "html.parser",
        ).get_text(" ", strip=True)
        label = re.sub(
            r"\s+(?:gross\s+regional\s+domestic\s+product|"
            r"gross\s+domestic\s+product/gross\s+regional\s+domestic\s+product)$",
            "",
            label,
            flags=re.IGNORECASE,
        )
        label = re.sub(r"\s+", " ", label).strip().casefold()
        if label not in aggregate_labels:
            continue

        value = _row_value(row, col_id)
        if value is not None:
            return {target: value}

    return {}


def _rows_are_children_of_target(table, targets):
    """
    True jika tabel jelas berisi anak-wilayah dari target
    (mis. target "Prov. Kalimantan Selatan", tabel 13 kab/kota Kalsel).
    """
    rows = table.get("rows") or []
    if len(rows) < 2:
        return False

    kodes = [
        row.get("kode_wilayah")
        for row in rows
        if isinstance(row.get("kode_wilayah"), int)
    ]

    if not kodes:
        return False

    # Semua kode ber-prefix provinsi yang sama (mis. 63xxxxx).
    provinces = {kode // 100_000 for kode in kodes}
    if len(provinces) != 1:
        return False

    # Tabel tidak memuat baris provinsi itu sendiri.
    if any(kode % 1_000_000 == 0 for kode in kodes):
        return False

    return True


def _target_is_province(targets):
    return any(
        normalize_region(t).startswith("prov.")
        for t in targets or []
    )


# =====================================================================
# 2. Jalur lama (RSC datacontent / __NEXT_DATA__ / value_raw) — fallback
# =====================================================================

def _extract_from_rsc_payload(html):
    """
    Extract data dari BPS React Server Components (RSC) payload.

    BPS SPA mengirim data via self.__next_f.push([1,"...json..."]).
    Cari object dengan key "datacontent" yang berisi data tabel.
    """
    for match in re.finditer(
        r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
        html,
    ):
        try:
            raw = json.loads('"' + match.group(1) + '"')
        except (json.JSONDecodeError, ValueError):
            continue

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            colon = line.find(":")
            if colon < 1:
                continue
            payload = line[colon + 1:]
            if not payload or payload[0] not in "{[":
                continue
            try:
                parsed = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue

            data_obj = parsed
            if isinstance(parsed, list) and len(parsed) >= 4 and isinstance(parsed[3], dict):
                data_obj = parsed[3]

            if isinstance(data_obj, dict) and "datacontent" in data_obj:
                return data_obj

    return None


def _find_nested_datacontent(value):
    if isinstance(value, dict):
        if isinstance(value.get("datacontent"), dict):
            return value
        for nested in value.values():
            found = _find_nested_datacontent(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_nested_datacontent(nested)
            if found is not None:
                return found
    return None


def _year_options_from_page(html):
    text = _unescape_next_f(html)
    options, _ = _find_json_after(text, "listTahun")
    if not isinstance(options, list):
        return []
    return [
        option for option in options
        if isinstance(option, dict)
        and str(option.get("th", "")).isdigit()
        and str(option.get("th_id", "")).isdigit()
    ]


def _discover_bps_table_action_id(url, html):
    script_urls = [
        urljoin(url, script.get("src"))
        for script in BeautifulSoup(html, "html.parser").find_all(
            "script", src=True
        )
    ]
    webpack_url = next(
        (
            script_url for script_url in script_urls
            if re.search(r"/webpack-[^/]+\.js(?:\?|$)", script_url)
        ),
        None,
    )

    action_pattern = re.compile(
        r'createServerReference\)\(["\']([a-f0-9]{40,64})["\']'
        r'[^)]{0,240}["\']getDataTable["\']'
    )

    if not webpack_url:
        raise RuntimeError(
            "Halaman BPS menggunakan bundler Next.js tanpa manifest "
            "webpack; gunakan fallback tabel hasil render browser."
        )

    cache_key = (urlsplit(url).netloc.lower(), webpack_url)
    cached = _BPS_ACTION_CACHE.get(cache_key)
    if cached:
        return cached

    response = requests.get(
        webpack_url,
        headers=PAGE_REQUEST_HEADERS,
        timeout=20,
    )
    response.raise_for_status()
    runtime = response.text
    runtime_start = runtime.find("r.u=e=>")
    runtime_end = runtime.find("r.miniCssF", runtime_start)
    if runtime_start < 0 or runtime_end < 0:
        raise RuntimeError("Manifest chunk Next.js tidak dikenali.")
    runtime = runtime[runtime_start:runtime_end]

    chunk_paths = re.findall(
        r'\d+===e\?"(static/chunks/[^"]+\.js)"',
        runtime,
    )
    chunk_paths.extend(
        f"static/chunks/{chunk_id}.{content_hash}.js"
        for chunk_id, content_hash in re.findall(
            r'(\d+):"([a-f0-9]{16})"',
            runtime,
        )
    )

    errors = []
    for chunk_path in dict.fromkeys(chunk_paths):
        chunk_url = urljoin(
            f"{urlsplit(url).scheme}://{urlsplit(url).netloc}/_next/",
            chunk_path,
        )
        try:
            chunk_response = requests.get(
                chunk_url,
                headers=PAGE_REQUEST_HEADERS,
                timeout=15,
            )
            chunk_response.raise_for_status()
        except requests.RequestException as error:
            errors.append(f"{chunk_path}: {error.__class__.__name__}")
            continue

        match = action_pattern.search(chunk_response.text)
        if match:
            action_id = match.group(1)
            _BPS_ACTION_CACHE[cache_key] = action_id
            return action_id

    detail = f" ({'; '.join(errors[:3])})" if errors else ""
    raise RuntimeError(
        f"Action tabel BPS getDataTable tidak ditemukan{detail}."
    )


def _parse_flight_datacontent(body):
    for line in str(body or "").splitlines():
        _row_id, separator, payload = line.partition(":")
        if not separator:
            continue
        try:
            value = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        data = _find_nested_datacontent(value)
        if data is not None:
            return data
    return None


def _fetch_bps_year_data(url, html, tahun):
    options = _year_options_from_page(html)
    if not options:
        return "unsupported", None, None

    year_option = next(
        (option for option in options if str(option["th"]) == str(tahun)),
        None,
    )
    available_years = sorted(
        (int(option["th"]) for option in options),
        reverse=True,
    )
    if year_option is None:
        return "unavailable", None, available_years

    path_parts = [
        unquote(part)
        for part in urlsplit(url).path.split("/")
        if part
    ]
    try:
        table_index = path_parts.index("statistics-table")
        locale = path_parts[table_index - 1]
        table_id = path_parts[table_index + 2]
    except (ValueError, IndexError):
        return "error", None, "Struktur URL tabel BPS tidak dikenali."

    try:
        action_id = _discover_bps_table_action_id(url, html)
        response = requests.post(
            url,
            headers={
                **PAGE_REQUEST_HEADERS,
                "Accept": "text/x-component",
                "Content-Type": "text/plain;charset=UTF-8",
                "Next-Action": action_id,
                "Referer": url,
            },
            data=json.dumps(
                [{
                    "locale": locale,
                    "id": table_id,
                    "year": str(year_option["th_id"]),
                }]
            ),
            timeout=30,
        )
        response.raise_for_status()
    except (requests.RequestException, RuntimeError) as error:
        return "error", None, str(error)[:250]

    data = _parse_flight_datacontent(response.text)
    if data is None:
        return (
            "error",
            None,
            "Respons pemilih tahun BPS tidak memuat datacontent.",
        )

    selected_years = data.get("tahun") or []
    if not any(
        isinstance(item, dict)
        and str(item.get("label")) == str(tahun)
        for item in selected_years
    ):
        return (
            "error",
            None,
            f"Respons BPS tidak sesuai tahun yang diminta ({tahun}).",
        )

    if data.get("data-availability") != "available":
        return "unavailable", None, available_years

    return "success", data, available_years


def _normalize_combined_rsc_keys(data_obj):
    datacontent = data_obj.get("datacontent")
    if not isinstance(datacontent, dict):
        return data_obj

    regions = [
        str(item.get("val"))
        for item in data_obj.get("vervar", [])
        if isinstance(item, dict) and item.get("val") is not None
    ]
    variables = [
        str(item.get("val"))
        for item in data_obj.get("var", [])
        if isinstance(item, dict) and item.get("val") is not None
    ]
    turvars = [
        str(item.get("val"))
        for item in data_obj.get("turvar", [])
        if isinstance(item, dict) and item.get("val") is not None
    ] or [""]
    turtahuns = [
        str(item.get("val"))
        for item in data_obj.get("turtahun", [])
        if isinstance(item, dict) and item.get("val") is not None
    ] or [""]
    years = [
        str(item.get("val"))
        for item in data_obj.get("tahun", [])
        if isinstance(item, dict) and item.get("val") is not None
    ]

    normalized = {}
    for key, value in datacontent.items():
        key_text = str(key)
        if " " in key_text:
            normalized[key_text] = value
            continue

        matched_parts = None
        for region_id in sorted(regions, key=len, reverse=True):
            if not key_text.startswith(region_id):
                continue
            for variable_id in sorted(variables, key=len, reverse=True):
                variable_start = len(region_id)
                if not key_text.startswith(variable_id, variable_start):
                    continue
                turvar_start = variable_start + len(variable_id)
                for turvar_id in sorted(turvars, key=len, reverse=True):
                    if not key_text.startswith(turvar_id, turvar_start):
                        continue
                    year_start = turvar_start + len(turvar_id)
                    for year_id in sorted(years, key=len, reverse=True):
                        if not key_text.startswith(year_id, year_start):
                            continue
                        remainder = key_text[year_start + len(year_id):]
                        if remainder in turtahuns:
                            matched_parts = (
                                region_id,
                                variable_id,
                                turvar_id or "0",
                                year_id,
                                remainder,
                            )
                            break
                    if matched_parts:
                        break
                if matched_parts:
                    break
            if matched_parts:
                break

        normalized[
            " ".join(matched_parts) if matched_parts else key_text
        ] = value

    normalized_data = dict(data_obj)
    normalized_data["datacontent"] = normalized
    return normalized_data


def _pick_best_from_rsc(data_obj, metric_terms, target_wilayah, tahun):
    """Pilih nilai terbaik dari RSC datacontent berdasarkan metric + tahun."""
    if isinstance(target_wilayah, (list, tuple, set)):
        return _pick_regions_from_rsc(
            data_obj,
            metric_terms,
            target_wilayah,
            tahun,
        )
    if target_wilayah:
        mapped = _pick_regions_from_rsc(
            data_obj,
            metric_terms,
            [target_wilayah],
            tahun,
        )
        if mapped:
            return mapped.get(target_wilayah)

    datacontent = data_obj.get("datacontent", {})
    vervar_list = data_obj.get("vervar", [])
    tahun_list = data_obj.get("tahun", [])

    if not datacontent or not vervar_list:
        return None

    tahun_str = str(tahun)
    tahun_ids = set()
    for t in tahun_list:
        if isinstance(t, dict):
            label = str(t.get("label", ""))
            if tahun_str in label:
                tahun_ids.add(t.get("val"))
        elif str(t) == tahun_str:
            tahun_ids.add(t)

    target_lower = (target_wilayah or "").lower()

    candidates = []
    for key, value in datacontent.items():
        numeric_value = parse_number_id(value)
        if (
            numeric_value is None
            or not _within_metric_range(numeric_value, metric_terms)
        ):
            continue
        key_str = str(key)
        parts = key_str.split()
        if len(parts) < 2:
            continue

        score = 0.0

        if target_lower:
            vervar_id = parts[0]
            for v in vervar_list:
                if isinstance(v, dict) and str(v.get("val")) == vervar_id:
                    label = str(v.get("label", "")).lower()
                    if target_lower in label or label in target_lower:
                        score += 3.0
                    elif any(w in label for w in target_lower.split() if len(w) > 3):
                        score += 1.5
                    break

        for term in metric_terms:
            if term in key_str:
                score += 1.0
            else:
                synonyms = METRIC_SYNONYMS.get(term, [])
                if any(syn in key_str for syn in synonyms):
                    score += 0.7

        if tahun_ids and len(parts) >= 3:
            for pid in parts[1:]:
                if pid.isdigit() and int(pid) in tahun_ids:
                    score += 2.0
                    break

        candidates.append((numeric_value, score, key_str))

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c[1])
    return best[0]


def _pick_regions_from_rsc(data_obj, metric_terms, targets, tahun):
    data_obj = _normalize_combined_rsc_keys(data_obj)
    datacontent = data_obj.get("datacontent", {})
    regions = data_obj.get("vervar", [])
    variables = data_obj.get("var", [])
    years = data_obj.get("tahun", [])
    if not datacontent or not regions:
        return None

    year_ids = {
        str(item.get("val"))
        for item in years
        if isinstance(item, dict)
        and str(tahun) in str(item.get("label", ""))
    }
    if not year_ids:
        year_ids = {str(tahun)}

    variable_scores = {}
    for item in variables:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").lower()
        score = 0.0
        for term in metric_terms:
            if len(term) < 3:
                continue
            if term in label:
                score += 1.0
            elif any(
                synonym in label
                for synonym in METRIC_SYNONYMS.get(term, [])
            ):
                score += 0.7
        if score:
            variable_scores[str(item.get("val"))] = score

    if not variable_scores:
        return None

    target_ids = {}
    for target in targets:
        for item in regions:
            label = BeautifulSoup(
                str(item.get("label") or ""),
                "html.parser",
            ).get_text(" ", strip=True) if isinstance(item, dict) else ""
            if (
                isinstance(item, dict)
                and regions_match(label, target)
            ):
                target_ids[str(item.get("val"))] = target
                break

    values = {}
    for key, raw_value in datacontent.items():
        parts = str(key).split()
        if len(parts) < 4:
            continue
        region_id, variable_id = parts[0], parts[1]
        if region_id not in target_ids or variable_id not in variable_scores:
            continue
        if not any(part in year_ids for part in parts[2:]):
            continue
        value = parse_number_id(raw_value)
        if value is None or not _within_metric_range(value, metric_terms):
            continue
        target = target_ids[region_id]
        previous = values.get(target)
        if previous is None or variable_scores[variable_id] > previous[0]:
            values[target] = (variable_scores[variable_id], value)

    return {
        target: scored_value[1]
        for target, scored_value in values.items()
    } or None


def _extract_bps_action_result(
    url,
    html,
    tahun,
    description=None,
    wilayah=None,
    nama_indikator=None,
):
    status, data, detail = _fetch_bps_year_data(url, html, tahun)
    if status == "unsupported":
        return None

    from models import ExtractionResult

    if status == "unavailable":
        return ExtractionResult(
            status="DATA_TIDAK_ADA",
            value=None,
            confidence=0.0,
            source_type="bps_table_action",
            message=(
                f"Tahun {tahun} tidak tersedia di tabel BPS. "
                f"Tahun tersedia: {detail}."
            ),
            evidence=[f"list_tahun={detail}"],
        )
    if status == "error":
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_table_action",
            message=f"Gagal mengambil tahun {tahun} dari tabel BPS: {detail}",
            evidence=[f"tahun_diminta={tahun}"],
        )

    from engine.instructions import parse_instruction

    metrics = _metric_terms(description, nama_indikator)
    instruction = parse_instruction(
        " ".join(
            part for part in (
                str(description or ""),
                str(nama_indikator or ""),
            ) if part
        ),
        default_wilayah=str(wilayah or ""),
    )
    values = _pick_regions_from_rsc(
        data,
        metrics,
        instruction.target_wilayahs or [],
        int(tahun),
    )

    requested_parent = str(wilayah or "").strip()
    if (
        values
        and instruction.target_wilayahs
        and _target_is_province([requested_parent])
        and not any(
            regions_match(target, requested_parent)
            for target in instruction.target_wilayahs
        )
    ):
        parent_values = _pick_regions_from_rsc(
            data,
            metrics,
            [requested_parent],
            int(tahun),
        )
        if parent_values:
            values = {**parent_values, **values}

    if not values and not instruction.target_wilayahs:
        region_labels = [
            BeautifulSoup(
                str(item.get("label") or ""),
                "html.parser",
            ).get_text(" ", strip=True)
            for item in data.get("vervar", [])
            if isinstance(item, dict)
        ]
        values = _pick_regions_from_rsc(
            data,
            metrics,
            region_labels,
            int(tahun),
        )
        if values:
            values = {
                _canonical_region(region): value
                for region, value in values.items()
            }

    if not values:
        return ExtractionResult(
            status="DATA_TIDAK_ADA",
            value=None,
            confidence=0.0,
            source_type="bps_table_action",
            message=(
                "Tabel BPS berhasil dibaca tetapi tidak memuat nilai "
                f"yang cocok untuk wilayah/tahun {tahun}."
            ),
            evidence=[
                f"tahun={tahun}",
                f"wilayah={instruction.target_wilayahs or 'semua'}",
            ],
        )

    return ExtractionResult(
        status="SUCCESS",
        value=values,
        confidence=0.95,
        source_type="bps_table_action",
        message=f"Data tabel BPS tahun {tahun} berhasil dibaca.",
        evidence=[
            f"tahun={tahun}",
            f"baris={len(values)}",
            f"tahun_tersedia={detail or 'unknown'}",
        ],
    )


def _extract_bps_table_detail(
    url,
    tahun,
    description=None,
    wilayah=None,
    nama_indikator=None,
):
    from models import ExtractionResult
    from engine.instructions import parse_instruction

    html = download_html(
        with_year_param(url, tahun),
        tag="BPS-TABLE",
    )
    metrics = _metric_terms(description, nama_indikator)
    instruction = parse_instruction(
        " ".join(
            part for part in (
                str(description or ""),
                str(nama_indikator or ""),
            ) if part
        ),
        default_wilayah=str(wilayah or ""),
    )
    instruction.metric_terms = metrics

    table = parse_bps_statistics_table(html)
    if table is None:
        action_result = _extract_bps_action_result(
            url,
            html,
            int(tahun),
            description,
            wilayah,
            nama_indikator,
        )
        if action_result is not None:
            return action_result

    if table is not None:
        page_year = table.get("tahun_data") or extract_table_year(html)
        if page_year is not None and int(page_year) != int(tahun):
            action_result = _extract_bps_action_result(
                url,
                html,
                int(tahun),
                description,
                wilayah,
                nama_indikator,
            )
            if action_result is not None:
                return action_result
            return None
        col_id, _meta = _pick_column(
            table,
            metrics,
            description or nama_indikator,
        )
        values = _match_rows(
            table,
            col_id,
            instruction.target_wilayahs,
        )
        if values:
            return ExtractionResult(
                status="SUCCESS",
                value=values,
                confidence=0.90,
                source_type="bps_table_struct",
                evidence=[f"tahun_data={page_year}", f"kolom={col_id}"],
            )

    rsc_data = _extract_from_rsc_payload(html)
    if rsc_data is not None:
        values = _pick_best_from_rsc(
            rsc_data,
            metrics,
            instruction.target_wilayahs,
            int(tahun),
        )
        if values:
            return ExtractionResult(
                status="SUCCESS",
                value=values,
                confidence=0.85,
                source_type="bps_table_rsc",
                evidence=[f"tahun={tahun}", f"wilayah={len(values)}"],
            )

    try:
        frames = pd.read_html(
            StringIO(html),
            decimal=",",
            thousands=".",
        )
    except ValueError:
        frames = []
    if frames:
        from engine.tabular import extract_from_dataframes

        result = extract_from_dataframes(
            frames,
            instruction,
            int(tahun),
            source_type="bps_table_html",
        )
        if result.value is not None and result.status in {"SUCCESS", "REVIEW"}:
            return result

    return None


def _extract_from_next_data(data, metric_terms, target_wilayah, tahun):
    """Recursive search di __NEXT_DATA__ untuk menemukan tabel data."""
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                result = _pick_best_from_table(
                    val, metric_terms, target_wilayah, tahun
                )
                if result is not None:
                    return result

        for key, val in data.items():
            if key.startswith("__"):
                continue
            result = _extract_from_next_data(val, metric_terms, target_wilayah, tahun)
            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = _extract_from_next_data(item, metric_terms, target_wilayah, tahun)
            if result is not None:
                return result

    return None


def _pick_best_from_table(rows, metric_terms, target_wilayah, tahun):
    """Pilih baris terbaik dari tabel berdasarkan metric_terms dan wilayah."""
    if not rows or not isinstance(rows, list):
        return None

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        for key, val in row.items():
            if not isinstance(val, (int, float)):
                continue
            if val <= 0:
                continue

            key_lower = key.lower()

            wilayah_score = 0
            if target_wilayah:
                target_lower = target_wilayah.lower()
                if target_lower in key_lower or key_lower in target_lower:
                    wilayah_score = 2.0
                elif any(part in key_lower for part in target_lower.split() if len(part) > 3):
                    wilayah_score = 1.0

            metric_score = 0
            for term in metric_terms:
                if term in key_lower:
                    metric_score += 1.0
                else:
                    synonyms = METRIC_SYNONYMS.get(term, [])
                    if any(syn in key_lower for syn in synonyms):
                        metric_score += 0.7

            tahun_score = 0
            if str(tahun) in str(row.values()):
                tahun_score = 1.0

            total_score = wilayah_score + metric_score + tahun_score

            candidates.append((val, total_score, key))

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c[1])
    return best[0]


def _extract_value_raw_with_context(html, metric_terms):
    """Extract value_raw dari HTML dengan context scoring."""
    candidates = []

    for match in re.finditer(
        r'value_raw\D{0,30}(\d[\d., ]*\d|\d)',
        html,
    ):
        raw = match.group(1)
        text = raw.strip().replace(" ", "")

        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif "." in text:
            text = text.replace(".", "")

        try:
            nilai = float(text)
        except ValueError:
            continue

        start = max(0, match.start() - 300)
        end = min(len(html), match.end() + 300)
        context = html[start:end].lower()

        context_score = 0
        for term in metric_terms:
            if term in context:
                context_score += 1.0
            else:
                synonyms = METRIC_SYNONYMS.get(term, [])
                if any(syn in context for syn in synonyms):
                    context_score += 0.7

        if metric_terms:
            context_score /= max(len(metric_terms), 1)

        candidates.append((nilai, context_score))

    if not candidates:
        return None

    best_by_context = max(candidates, key=lambda c: c[1])
    if best_by_context[1] >= 0.5 and best_by_context[0] > 0:
        return best_by_context[0]

    all_values = [c[0] for c in candidates if c[0] > 0]
    if all_values:
        return max(all_values)

    return None


# =====================================================================
# 3. Entry point
# =====================================================================

def try_bps_table(
    url, tahun, description=None, wilayah=None, nama_indikator=None
):
    """
    Template universal untuk tabel BPS /statistics-table/.

    Jalur utama: parser terstruktur (tahun + kolom + seluruh baris
    wilayah + konversi satuan). Fallback: RSC datacontent /
    __NEXT_DATA__ / value_raw seperti sebelumnya.

    Return ExtractionResult atau None.
    """
    tahun = int(tahun)
    lower_url = str(url or "").lower()

    if "bps.go.id" not in lower_url:
        return None

    if _is_bps_statistics_table_listing(url):
        try:
            detail_url, _catalog_html, catalog_evidence = (
                _find_catalog_detail_url(
                    url,
                    description,
                    nama_indikator,
                )
            )
        except Exception as error:
            from models import ExtractionResult

            return ExtractionResult(
                status="NEED_PARSER",
                source_type="bps_table_catalog",
                message=f"Gagal membaca katalog tabel BPS: {str(error)[:200]}",
            )

        if not detail_url:
            from models import ExtractionResult

            return ExtractionResult(
                status="NEED_PARSER",
                source_type="bps_table_catalog",
                message="Katalog BPS tidak memiliki tabel yang cocok.",
                evidence=catalog_evidence,
            )

        result = _extract_bps_table_detail(
            detail_url,
            tahun,
            description=description,
            wilayah=wilayah,
            nama_indikator=nama_indikator,
        )
        if result is None:
            from models import ExtractionResult

            result = ExtractionResult(
                status="NEED_PARSER",
                source_type="bps_table_catalog",
                message="Detail tabel BPS tidak menghasilkan data yang cocok.",
            )
        result.evidence.extend(
            [
                f"catalog_url={url}",
                f"table_url={detail_url}",
                *catalog_evidence,
            ]
        )
        return result

    if "/statistics-table/" not in lower_url:
        return None

    cache_key = (
        str(url or "").strip(),
        tahun,
        str(description or ""),
        str(wilayah or ""),
        str(nama_indikator or ""),
    )
    if cache_key in _CACHE:
        from models import ExtractionResult
        return ExtractionResult(
            status="SUCCESS",
            value=_CACHE[cache_key],
            confidence=0.95,
            source_type="bps_table_cached",
            message="Data dari cache.",
            evidence=["cached=true"],
        )

    metric_terms = _metric_terms(description, nama_indikator)
    rentang = detect_rentang(metric_terms)

    print(
        f"    [BPS-TABLE] Template: metric={metric_terms}, "
        f"rentang={rentang}, tahun={tahun}"
    )

    target_url = with_year_param(url, tahun)

    try:
        html = download_html(target_url, tag="BPS-TABLE")
    except Exception as error:
        from models import ExtractionResult
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_table",
            message=f"Gagal download: {str(error)[:200]}",
            evidence=[],
        )

    # ------------------------------------------------------------
    # A. Jalur terstruktur (universal penuh)
    # ------------------------------------------------------------
    table = parse_bps_statistics_table(html)

    if table is None:
        action_result = _extract_bps_action_result(
            url,
            html,
            tahun,
            description,
            wilayah,
            nama_indikator,
        )
        if action_result is not None:
            if action_result.status == "SUCCESS":
                _CACHE[cache_key] = action_result.value
            return action_result

    if table is not None:
        # Gerbang tahun: halaman wajib memuat tahun yang diminta.
        tahun_data = table.get("tahun_data")
        if tahun_data is None:
            tahun_data = extract_table_year(html)

        if tahun_data is not None and int(tahun_data) != tahun:
            action_result = _extract_bps_action_result(
                url,
                html,
                tahun,
                description,
                wilayah,
                nama_indikator,
            )
            if action_result is not None:
                if action_result.status == "SUCCESS":
                    _CACHE[cache_key] = action_result.value
                return action_result

            available = table.get("list_tahun") or []
            from models import ExtractionResult

            if available and tahun not in available:
                return ExtractionResult(
                    status="DATA_TIDAK_ADA",
                    value=None,
                    confidence=0.0,
                    source_type="bps_table",
                    message=(
                        f"Tahun {tahun} tidak tersedia di tabel BPS. "
                        f"Tahun tersedia: {available}."
                    ),
                    evidence=[f"list_tahun={available}"],
                )

            return ExtractionResult(
                status="NEED_PARSER",
                value=None,
                confidence=0.0,
                source_type="bps_table",
                message=(
                    f"Halaman BPS memuat tahun {tahun_data}, "
                    f"bukan {tahun}."
                ),
                evidence=[f"tahun_data={tahun_data}"],
            )

        col_id, col_meta = _pick_column(
            table,
            metric_terms,
            f"{description or ''} {nama_indikator or ''}",
        )

        if col_id is None:
            from models import ExtractionResult
            is_wrong_poverty_measure = (
                _is_poverty_rate_request(
                    f"{description or ''} {nama_indikator or ''}"
                )
                and any(
                    isinstance(meta, dict)
                    and _is_poverty_line_variable(
                        meta.get("nama_variabel")
                    )
                    for meta in (table.get("kolom") or {}).values()
                )
            )
            return ExtractionResult(
                status="NEED_PARSER",
                value=None,
                confidence=0.0,
                source_type="bps_table_struct",
                message=(
                    "Tabel yang dipilih berisi Garis Kemiskinan, bukan "
                    "persentase penduduk miskin."
                    if is_wrong_poverty_measure
                    else
                    "Struktur tabel BPS terbaca tetapi tidak ada "
                    "kolom variabel yang bisa dibaca."
                ),
                evidence=[f"baris={len(table.get('rows') or [])}"],
            )

        from engine.instructions import parse_instruction

        instruction = parse_instruction(
            str(description or ""),
            default_wilayah=str(wilayah or ""),
        )
        targets = instruction.target_wilayahs or []

        matched = _match_rows(table, col_id, targets)

        if not matched:
            matched = _match_provincial_sector_row(
                table,
                col_id,
                targets,
                metric_terms,
            )

        # Some provincial tables put the requested sector or aggregate in
        # the rows and identify the province only in the title.
        if not matched:
            matched = _match_provincial_aggregate_row(
                table,
                col_id,
                targets,
                metric_terms,
            )

        # Tabel anak-wilayah (kab/kota) saat target hanya provinsi:
        # ambil semua baris yang ada — jangan mengarang baris provinsi.
        if (
            not matched
            and targets
            and _target_is_province(targets)
            and _rows_are_children_of_target(table, targets)
        ):
            matched = _match_rows(table, col_id, targets=None)

        if matched:
            # Konversi satuan: dari metadata kolom/halaman ke satuan
            # yang diminta indikator (mis. "Rp Juta").
            page_unit = normalize_unit(
                col_meta.get("unit_multiplier_desc") or ""
            ) or detect_page_unit(html)
            target_unit = detect_unit_in_text(description)

            evidence = [
                f"tahun_data={tahun_data}",
                f"kolom={col_id}",
                f"baris={len(matched)}",
                f"unit_asal={page_unit or '-'}",
                f"unit_tujuan={target_unit or '-'}",
            ]

            if page_unit and target_unit:
                matched = {
                    region: convert_unit(nilai, page_unit, target_unit)
                    for region, nilai in matched.items()
                }

            nama_kolom = str(
                col_meta.get("nama_variabel") or ""
            ).strip()

            _CACHE[cache_key] = matched

            from models import ExtractionResult
            print(
                f"    [BPS-TABLE] Terstruktur: {len(matched)} baris, "
                f"kolom='{nama_kolom or col_id}', "
                f"tahun={tahun_data}, "
                f"satuan={page_unit or '-'}->{target_unit or '-'}, "
                f"nilai={matched}"
            )
            return ExtractionResult(
                status="SUCCESS",
                value=dict(matched),
                confidence=0.90,
                source_type="bps_table_struct",
                message=(
                    f"Tabel BPS terbaca penuh: {len(matched)} wilayah, "
                    f"kolom '{nama_kolom or col_id}', tahun {tahun_data}."
                ),
                evidence=evidence,
            )

        # Struktur & tahun terbaca benar tetapi tidak ada baris yang
        # cocok: data memang tidak ada untuk wilayah yang diminta.
        # JANGAN lanjut ke jalur heuristik (max() bisa salah senyap).
        from models import ExtractionResult
        labels = [
            str(row.get("label") or "")
            for row in (table.get("rows") or [])[:20]
        ]
        return ExtractionResult(
            status="DATA_TIDAK_ADA",
            value=None,
            confidence=0.0,
            source_type="bps_table_struct",
            message=(
                "Tabel BPS terbaca tetapi tidak memuat baris untuk "
                f"wilayah yang diminta (target={targets})."
            ),
            evidence=[f"baris_tabel={labels}"],
        )

    # ------------------------------------------------------------
    # B. Jalur lama (fallback — hanya untuk halaman tanpa struktur
    #    __next_f yang dikenali)
    # ------------------------------------------------------------

    # Method 1: RSC payload (React Server Components) — BPS terbaru.
    rsc_data = _extract_from_rsc_payload(html)
    if rsc_data is not None:
        nilai = _pick_best_from_rsc(rsc_data, metric_terms, wilayah, tahun)
        if nilai is not None and _within_metric_range(nilai, metric_terms):
            _CACHE[cache_key] = _legacy_value(nilai, description, wilayah)
            from models import ExtractionResult
            return ExtractionResult(
                status="SUCCESS",
                value=_CACHE[cache_key],
                confidence=0.85,
                source_type="bps_table_rsc",
                message=f"Data dari RSC payload: {nilai}",
                evidence=[f"rsc_value={nilai}"],
            )

    # Method 2: __NEXT_DATA__ (Next.js Pages Router — BPS lama).
    next_match = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if next_match:
        try:
            data = json.loads(next_match.group(1))
            nilai = _extract_from_next_data(data, metric_terms, wilayah, tahun)
            if nilai is not None and _within_metric_range(nilai, metric_terms):
                _CACHE[cache_key] = _legacy_value(nilai, description, wilayah)
                from models import ExtractionResult
                return ExtractionResult(
                    status="SUCCESS",
                    value=_CACHE[cache_key],
                    confidence=0.85,
                    source_type="bps_table_nextjs",
                    message=f"Data dari __NEXT_DATA__: {nilai}",
                    evidence=[f"nextjs_value={nilai}"],
                )
        except (json.JSONDecodeError, ValueError):
            pass

    nilai = _extract_value_raw_with_context(html, metric_terms)
    if nilai is not None and _within_metric_range(nilai, metric_terms):
        _CACHE[cache_key] = _legacy_value(nilai, description, wilayah)
        from models import ExtractionResult
        return ExtractionResult(
            status="SUCCESS",
            value=_CACHE[cache_key],
            confidence=0.75,
            source_type="bps_table_value_raw",
            message=f"Data dari value_raw: {nilai}",
            evidence=[f"value_raw={nilai}"],
        )

    from models import ExtractionResult

    # Halaman shell-kosong (BPS mengirim halaman tanpa data saat tahun
    # diminta tidak tersedia). Semua jalur gagal karena memang tidak
    # ada data — bukan format tak dikenal. Laporkan DATA_TIDAK_ADA
    # supaya worker tidak retry percuma.
    has_any_data = (
        "value_raw" in html
        or "datacontent" in html
        or "__NEXT_DATA__" in html
        or "judul_tabel" in html
    )
    if not has_any_data:
        return ExtractionResult(
            status="DATA_TIDAK_ADA",
            value=None,
            confidence=0.0,
            source_type="bps_table",
            message=(
                f"Halaman BPS tidak memuat data tabel untuk tahun "
                f"{tahun} (kemungkinan tahun tidak tersedia)."
            ),
            evidence=[f"tahun_diminta={tahun}"],
        )

    return ExtractionResult(
        status="NEED_PARSER",
        value=None,
        confidence=0.0,
        source_type="bps_table",
        message=f"Tidak ditemukan data BPS yang cocok (metric={metric_terms})",
        evidence=[],
    )


def _legacy_value(nilai, description, wilayah):
    """
    Bungkus nilai tunggal jalur lama.

    Kunci = target wilayah indikator jika hanya satu, agar baris
    hasil tidak berlabel "__main__".
    """
    from engine.instructions import parse_instruction

    instruction = parse_instruction(
        str(description or ""),
        default_wilayah=str(wilayah or ""),
    )
    targets = instruction.target_wilayahs or []

    if len(targets) == 1:
        return {targets[0]: nilai}

    if wilayah:
        return {str(wilayah): nilai}

    return {"__main__": nilai}


__all__ = ["try_bps_table", "parse_bps_statistics_table"]
