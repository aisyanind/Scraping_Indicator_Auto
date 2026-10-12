"""
Ekstraktor DISCOVERY — untuk halaman portal/SPA yang datanya tidak langsung
di HTML (Next.js, React, dashboard client-side).

Alur:
1. Coba ekstrak tabel dari HTML asli (jika ada, mis. halaman server-render).
2. Discovery API: baca JS chunks halaman (Next.js/React), temukan endpoint
   API (mis. `/api/v1/datasets/{id}/table`), lalu fetch & ekstrak JSON.
   Template `${...}` diselesaikan memakai id yang didapat dari endpoint
   "list" (mis. `/api/v1/portal/datasets`).
3. Jika SPA, render dengan Selenium untuk mendapatkan DOM penuh.
4. Kumpulkan link kandidat (file data + artikel) dan ekstrak rekursif.
"""

import re
from io import StringIO
from urllib.parse import urljoin, urlparse

import pandas as pd
import requests

from config import REQUEST_HEADERS
from engine.detector import SourcePayload, detect_kind
from engine.tabular import extract_from_dataframes
from models import ExtractionResult

DATA_EXTENSIONS = (
    ".pdf", ".xlsx", ".xls", ".csv", ".zip", ".json",
)

DOWNLOAD_MARKERS = (
    "download", "/file", "attachment", "unduh", "file=",
)

MAX_DEPTH = 2
MAX_LINKS = 8
RENDER_WAIT = 3
MAX_JS_CHUNKS = 4
MAX_APIS = 10
API_PATH_HINTS = (
    "/api/", "/v1/", "/v2/", "/data/", "/dataset/",
    "/datasets/", "/table/", "/tables/", "/download/",
)


def _decode_html(content):
    return content.decode("utf-8", errors="replace")


def _iter_frames(html):
    try:
        return pd.read_html(StringIO(html))
    except Exception:
        return []


def _extract_tables(html, instruction, tahun, source_type="html"):
    frames = _iter_frames(html)

    if not frames:
        return None

    result = extract_from_dataframes(
        frames,
        instruction,
        tahun,
        source_type=source_type,
    )

    if result.status == "SUCCESS":
        return result

    return None


def _matches_instruction(link, text, instruction):
    terms = [
        term
        for term in instruction.metric_terms
        if len(term) >= 3
    ]

    haystack = f"{link} {text}".lower()

    return any(term in haystack for term in terms)


def _is_data_file(href):
    lowered = href.lower()
    path = urlparse(href).path.lower()
    return path.endswith(DATA_EXTENSIONS) or any(
        marker in lowered
        for marker in DOWNLOAD_MARKERS
    )


def _collect_links(html, base_url, instruction):
    """
    Kumpulkan link kandidat dari HTML (raw atau render).
    Return list url absolut, sudah dedupe & diurutkan prioritas.
    """
    candidates = []

    for href, text in re.findall(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        href = href.strip()
        text = re.sub(r"<[^>]+>", "", text or "").strip()

        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue

        lowered = href.lower()

        if not (
            _is_data_file(lowered)
            or _matches_instruction(href, text, instruction)
        ):
            continue

        absolute = urljoin(base_url, href)

        if absolute not in candidates:
            candidates.append(absolute)

    # Digital book/viewer sering menyembunyikan file di iframe/embed,
    # bukan pada tautan <a> yang terlihat oleh pengguna.
    for attribute in ("src", "data"):
        for href in re.findall(
            rf'<(?:iframe|embed|object)[^>]+{attribute}=["\']([^"\']+)',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            href = href.strip()
            if href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute = urljoin(base_url, href)
            if absolute not in candidates:
                candidates.append(absolute)

    # Data file diutamakan, lalu link yang cocok indikator.
    data_files = [
        link for link in candidates
        if _is_data_file(link)
    ]
    others = [
        link for link in candidates
        if link not in data_files
    ]

    return (data_files + others)[:MAX_LINKS]


def _run_sub_payload(payload, instruction, tahun):
    from engine.router import run_generic

    return run_generic(payload, instruction, tahun)


def _best_result(results):
    best = None

    for result in results:
        if result is None:
            continue
        if result.status not in {"SUCCESS", "REVIEW"}:
            continue
        if result.value is None:
            continue
        if best is None or result.confidence > best.confidence:
            best = result

    return best


def _fetch_link(link):
    response = requests.get(
        link,
        headers=REQUEST_HEADERS,
        timeout=30,
        allow_redirects=True,
    )
    response.raise_for_status()

    kind = detect_kind(
        response.content,
        content_type=(
            response.headers.get("Content-Type", "")
            .split(";")[0]
            .strip()
            .lower()
        ),
        url=response.url,
    )

    return SourcePayload(
        url=link,
        kind=kind,
        content=response.content,
        content_type=(
            response.headers.get("Content-Type", "")
            .split(";")[0]
            .strip()
            .lower()
        ),
        final_url=response.url,
    )


# ============================================================
# Discovery API dari JS chunks (Next.js/React)
# ============================================================

def _collect_js_chunk_urls(html, base_url):
    urls = []

    for match in re.findall(
        r'src=["\']([^"\']*\.js[^"\']*)["\']',
        html,
    ):
        src = match.strip()
        if src.startswith("#"):
            continue
        absolute = urljoin(base_url, src)
        if absolute not in urls:
            urls.append(absolute)

    # JS chunks yang dirujuk di dalam RSC payload (__next_f) — halaman
    # Next.js sering menyebut file chunk tanpa tag <script src> langsung.
    for match in re.findall(
        r'["\']([^"\']*static/chunks/[^"\']*\.js)["\']',
        html,
    ):
        path = match.strip()
        if not path.startswith(("http://", "https://")):
            path = path.lstrip("/")
        absolute = urljoin(base_url, path)
        if absolute not in urls:
            urls.append(absolute)

    return urls[:MAX_JS_CHUNKS]


def _fetch_js_chunks(js_urls):
    texts = []

    for url in js_urls:
        try:
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=8,
            )
            response.raise_for_status()
            texts.append(response.text)
        except Exception:
            continue

    return "\n".join(texts)


def _extract_api_templates(js_text):
    """
    Temukan pola endpoint API dari teks JS.
    Return list (template, is_concrete).
    Contoh ditemukan:
      "/api/v1/portal/datasets"                        (concrete)
      "/api/v1/datasets/${e.id}/table?page=1"          (template)
    """
    patterns = set()

    for match in re.finditer(
        r'["\'`]((?:https?://|/)[^"\'`\s]+)["\'`]',
        js_text,
    ):
        path = match.group(1).strip()
        lowered = path.lower()
        if not any(hint in lowered for hint in API_PATH_HINTS):
            continue
        if path.endswith(("/", ".js", ".css")):
            continue
        patterns.add(path)

    return patterns


def _resolve_api_candidates(templates, base_url):
    """
    Pisahkan template yang konkret vs yang memakai ${...}.
    Urutkan: endpoint dengan "dataset"/"table"/"data" didahulukan.
    """
    concrete = []
    templated = []

    for path in templates:
        if "${" in path:
            templated.append(path)
        else:
            concrete.append(urljoin(base_url, path))

    # Prioritaskan endpoint data (dataset, table, data) daripada
    # endpoint utility (stats, login, auth, visit).
    def _sort_key(url):
        low = url.lower()
        score = 0
        for kw in ("dataset", "table", "data", "download"):
            if kw in low:
                score -= 1
        for kw in ("stats", "login", "auth", "visit", "setting"):
            if kw in low:
                score += 10
        return score

    concrete.sort(key=_sort_key)

    return concrete[:MAX_APIS], templated[:MAX_APIS]


def _extract_json_result(content, instruction, tahun, source_type="api"):
    from extractors.json_api import extract_json

    payload = SourcePayload(
        url="",
        kind="json",
        content=content,
        content_type="application/json",
        final_url="",
    )

    return extract_json(payload, instruction, tahun)


def _result_has_target_region(result, instruction):
    """
    Cek apakah hasil ekstraksi benar-benar mengandung target wilayah.
    Mencegah endpoint utility (stats/login) terpilih padahal bukan data
    indikator per wilayah.
    """
    if result is None:
        return False

    if result.status not in {"SUCCESS", "REVIEW"}:
        return False

    if not instruction.target_wilayahs:
        return True

    if result.value is None:
        return False

    target_names = [
        target.lower()
        for target in instruction.target_wilayahs
    ]

    def _matches(value):
        text = re.sub(r"\s+", " ", str(value or "").lower()).strip()
        return any(
            re.sub(r"^(prov\.|kab\.|kota)\s+", "", target)
            in text
            for target in target_names
        )

    if isinstance(result.value, dict):
        return any(
            _matches(key)
            or _matches(val)
            for key, val in result.value.items()
        )

    return _matches(result.value)


def _discover_api(payload, instruction, tahun):
    """
    Coba temukan data dari API yang dipakai halaman SPA.

    1. Kumpulkan JS chunks -> cari endpoint `/api/...`.
    2. Fetch endpoint konkret -> jika JSON, coba ekstrak.
    3. Untuk template `${...}` (mis. dataset id), cari id dari endpoint
       "list" yang mengembalikan daftar object (punya field id/slug),
       lalu substitusi ke template dan coba lagi.
    """
    page_url = payload.final_url or payload.url
    raw_html = _decode_html(payload.content)

    js_urls = _collect_js_chunk_urls(raw_html, page_url)

    if not js_urls:
        return None

    js_text = _fetch_js_chunks(js_urls)

    if not js_text:
        return None

    templates = _extract_api_templates(js_text)

    if not templates:
        return None

    concrete, templated = _resolve_api_candidates(
        templates,
        page_url,
    )

    results = []

    # 1) Endpoint konkret.
    for url in concrete:
        try:
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=10,
            )
            response.raise_for_status()
            response.json()
        except Exception:
            continue

        result = _extract_json_result(
            response.content,
            instruction,
            tahun,
            source_type="api",
        )

        if result is not None and result.status == "SUCCESS":
            if _result_has_target_region(result, instruction):
                results.append(result)

        best = _best_result(results)
        if (
            best is not None
            and best.status == "SUCCESS"
            and best.confidence >= 0.9
        ):
            return best

    # 2) Template ${...}: butuh id. Ambil id dari endpoint yang
    #    mengembalikan daftar object (list endpoint / portal).
    ids = set()

    for url in concrete:
        try:
            response = requests.get(
                url,
                headers=REQUEST_HEADERS,
                timeout=10,
            )
            data = response.json()
        except Exception:
            continue

        def _collect_ids(value):
            if isinstance(value, dict):
                for key in ("id", "dataset_id", "slug"):
                    if key in value and value[key]:
                        ids.add(str(value[key]))
                for item in value.values():
                    _collect_ids(item)
            elif isinstance(value, list):
                for item in value:
                    _collect_ids(item)

        _collect_ids(data)

        if len(ids) > 5:
            break

    if not ids:
        return _best_result(results)

    for template in templated:
        for dataset_id in list(ids)[:5]:
            try:
                filled = template.replace(
                    "${",
                    str(dataset_id) + "__PLACEHOLDER__",
                )
            except Exception:
                continue
            # Ganti semua `${...}` dengan id.
            filled = re.sub(r"\$\{[^}]*\}", str(dataset_id), template)

            url = urljoin(page_url, filled)

            try:
                response = requests.get(
                    url,
                    headers=REQUEST_HEADERS,
                    timeout=10,
                )
                response.raise_for_status()
                response.json()
            except Exception:
                continue

            result = _extract_json_result(
                response.content,
                instruction,
                tahun,
                source_type="api",
            )

            if result is not None and result.status == "SUCCESS":
                if _result_has_target_region(result, instruction):
                    results.append(result)

            best = _best_result(results)
            if (
                best is not None
                and best.status == "SUCCESS"
                and best.confidence >= 0.9
            ):
                return best

    return _best_result(results)


def extract_discovery(payload, instruction, tahun, depth=0):
    page_url = payload.final_url or payload.url

    raw_html = _decode_html(payload.content)

    from extractors.flip_pdf import extract_flip_pdf

    try:
        flip_result = extract_flip_pdf(payload, instruction, tahun)
    except Exception as error:
        print(
            f"    [FLIP-PDF] Gagal extract: "
            f"{str(error).splitlines()[0][:200]}"
        )
        flip_result = None

    if flip_result is not None:
        return flip_result

    # 1) Coba tabel dari HTML asli (halaman server-render).
    direct = _extract_tables(
        raw_html,
        instruction,
        tahun,
        source_type="discovery:html",
    )

    if direct is not None:
        return direct

    # 2) Discovery API dari JS chunks (ringan, tanpa Selenium).
    if payload.kind == "spa" and depth == 0:
        api_result = _discover_api(payload, instruction, tahun)

        if (
            api_result is not None
            and api_result.status == "SUCCESS"
        ):
            return api_result

    html_sources = [raw_html]

    # 3) Render SPA atau HTML viewer yang kosong agar JavaScript dan
    # network API-nya dapat diamati browser.
    if payload.kind in {"html", "spa"} and depth < MAX_DEPTH:
        try:
            from extractors.browser import render_page_capture_network

            rendered, network_responses, screenshot = render_page_capture_network(
                page_url,
                wait_seconds=RENDER_WAIT,
            )
            print(
                f"    [DISCOVERY] Browser network responses="
                f"{len(network_responses)}"
            )

            for response_url, content, content_type in network_responses:
                if content_type.startswith("image/"):
                    from extractors.pdf import ocr_image

                    result = ocr_image(
                        content,
                        instruction,
                        tahun,
                    )
                    matches_target = result is not None and result.value is not None
                else:
                    response_kind = detect_kind(
                        content,
                        content_type=content_type,
                        url=response_url,
                    )
                    if response_kind == "pdf":
                        result = _run_sub_payload(
                            SourcePayload(
                                url=response_url,
                                kind="pdf",
                                content=content,
                                content_type=content_type,
                                final_url=response_url,
                            ),
                            instruction,
                            tahun,
                        )
                        matches_target = (
                            result is not None
                            and result.value is not None
                        )
                    else:
                        result = _extract_json_result(
                            content,
                            instruction,
                            tahun,
                            source_type="browser:network",
                        )
                        matches_target = _result_has_target_region(
                            result,
                            instruction,
                        )

                if (
                    result is not None
                    and result.status in {"SUCCESS", "REVIEW"}
                    and matches_target
                ):
                    result.evidence.append(f"api_url={response_url}")
                    return result

            if screenshot:
                from extractors.pdf import ocr_image

                screenshot_result = ocr_image(
                    screenshot,
                    instruction,
                    tahun,
                )
                if (
                    screenshot_result is not None
                    and screenshot_result.status in {"SUCCESS", "REVIEW"}
                    and screenshot_result.value is not None
                ):
                    screenshot_result.evidence.append(
                        "browser_screenshot_ocr"
                    )
                    return screenshot_result

            html_sources.append(rendered)
        except Exception as error:
            print(
                f"    [DISCOVERY] Render SPA gagal: "
                f"{str(error).splitlines()[0]}"
            )

        for html in html_sources[1:]:
            rendered_result = _extract_tables(
                html,
                instruction,
                tahun,
                source_type="discovery:render",
            )
            if rendered_result is not None:
                return rendered_result

    # 4) Kumpulkan link kandidat.
    links = []
    for html in html_sources:
        for link in _collect_links(html, page_url, instruction):
            if link not in links:
                links.append(link)

    print(
        f"    [DISCOVERY] {len(links)} link kandidat ditemukan "
        f"(depth={depth})."
    )

    results = []

    for link in links:
        try:
            sub_payload = _fetch_link(link)
        except Exception as error:
            print(
                f"    [DISCOVERY] fetch gagal {link[:80]}: "
                f"{str(error).splitlines()[0]}"
            )
            continue

        if sub_payload.kind in {"html", "spa"}:
            if depth < MAX_DEPTH:
                result = extract_discovery(
                    sub_payload,
                    instruction,
                    tahun,
                    depth=depth + 1,
                )
            else:
                result = _extract_tables(
                    _decode_html(sub_payload.content),
                    instruction,
                    tahun,
                    source_type="discovery:nested",
                )
        else:
            result = _run_sub_payload(
                sub_payload,
                instruction,
                tahun,
            )

        if result is not None:
            results.append(result)

        best_so_far = _best_result(results)

        if (
            best_so_far is not None
            and best_so_far.status == "SUCCESS"
            and best_so_far.confidence >= 0.9
        ):
            break

    best = _best_result(results)

    if best is not None:
        return best

    return ExtractionResult(
        status="NEED_PARSER",
        source_type="discovery",
        message=(
            "Portal/SPA dibuka, tetapi tidak ada link data / tabel "
            "yang berhasil diekstrak."
        ),
    )


__all__ = ["extract_discovery"]

