"""Discover PDF files on BPS pages and pass them to the shared PDF extractor."""

import copy
import re
import tempfile
import time
from collections import OrderedDict
from html import unescape as html_unescape
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from engine.instructions import parse_instruction
from engine.detector import SourcePayload
from extractors.pdf import extract_pdf
from models import ExtractionResult
from sources.bps_utils import download_html, download_pdf, find_pdf_url

_RESULT_CACHE = {}
_PDF_URL_CACHE = {}
_PDF_REFERER_CACHE = {}
_PDF_DOWNLOAD_CACHE = OrderedDict()
_PDF_DOWNLOAD_LOCKS = {}
_PDF_DOWNLOAD_CACHE_LOCK = Lock()
_PDF_DOWNLOAD_CACHE_MAX_ENTRIES = 12
_PDF_DOWNLOAD_SUCCESS_TTL = 600
_PDF_DOWNLOAD_FAILURE_TTL = 300
_BROWSER_DOWNLOAD_TIMEOUT = 180
_BROWSER_DOWNLOAD_IDLE_TIMEOUT = 20
_MAX_LISTING_PAGES = 5
_MAX_ARTICLE_PAGES = 10


def _download_pdf_via_browser(url, referer=None):
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException

    with tempfile.TemporaryDirectory(prefix="bps_pdf_") as download_dir:
        options = webdriver.ChromeOptions()
        options.page_load_strategy = "eager"
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--log-level=3")
        options.add_experimental_option(
            "prefs",
            {
                "download.default_directory": download_dir,
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "plugins.always_open_pdf_externally": True,
                "safebrowsing.enabled": True,
            },
        )

        driver = webdriver.Chrome(options=options)
        try:
            driver.set_page_load_timeout(_BROWSER_DOWNLOAD_TIMEOUT)
            driver.execute_cdp_cmd(
                "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": download_dir,
                },
            )
            try:
                driver.get(referer or url)
            except TimeoutException:
                pass

            if referer and referer != url:
                try:
                    link = driver.execute_script(
                        """
                        const target = arguments[0];
                        return Array.from(document.querySelectorAll("a[href]"))
                            .find(anchor => anchor.href === target) || null;
                        """,
                        url,
                    )
                    if link is not None:
                        link.click()
                    else:
                        driver.get(url)
                except Exception as click_error:
                    try:
                        driver.get(url)
                    except Exception as direct_error:
                        raise RuntimeError(
                            "klik tautan dari halaman sumber gagal "
                            f"({str(click_error).splitlines()[0][:120]}); "
                            "buka URL langsung juga gagal "
                            f"({str(direct_error).splitlines()[0][:120]})"
                        ) from direct_error

            deadline = time.monotonic() + _BROWSER_DOWNLOAD_TIMEOUT
            last_progress_at = time.monotonic()
            last_sizes = {}
            while time.monotonic() < deadline:
                files = list(Path(download_dir).iterdir())
                partial_downloads = [
                    path for path in files
                    if path.name.endswith(".crdownload")
                ]
                for path in files:
                    if path in partial_downloads or not path.is_file():
                        continue
                    content = path.read_bytes()
                    if content.startswith(b"%PDF"):
                        return content

                current_sizes = {
                    path: path.stat().st_size
                    for path in partial_downloads
                    if path.exists()
                }
                if current_sizes != last_sizes:
                    last_sizes = current_sizes
                    last_progress_at = time.monotonic()
                elif (
                    current_sizes
                    and time.monotonic() - last_progress_at
                    >= _BROWSER_DOWNLOAD_IDLE_TIMEOUT
                ):
                    partial_files = ", ".join(
                        f"{path.name} ({size} byte)"
                        for path, size in current_sizes.items()
                    )
                    raise RuntimeError(
                        "unduhan browser macet tanpa progres: "
                        f"{partial_files}"
                    )
                time.sleep(0.25)

            downloaded_names = ", ".join(
                f"{path.name} ({path.stat().st_size} byte)"
                for path in Path(download_dir).iterdir()
                if path.is_file()
            )
            detail = (
                f"unduhan browser tidak selesai: {downloaded_names}"
                if downloaded_names
                else "browser tidak menghasilkan file unduhan"
            )
            raise RuntimeError(detail)
        finally:
            driver.quit()


def _download_pdf(url, referer=None):
    try:
        return download_pdf(url, referer=referer)
    except Exception as http_error:
        try:
            content = _download_pdf_via_browser(url, referer=referer)
            print(
                "    [BPS-PDF] Unduhan HTTP gagal; "
                "PDF berhasil diambil lewat Chrome."
            )
            return content
        except Exception as browser_error:
            http_detail = _short_download_error(http_error)
            browser_detail = _short_download_error(browser_error)
            raise RuntimeError(
                f"HTTP: {http_detail}; Chrome: {browser_detail}"
            ) from browser_error


def _short_download_error(error):
    message = str(error).splitlines()[0]
    error_types = dict.fromkeys(
        re.findall(
            r"\b(ConnectionResetError|ConnectionReset|ConnectionError|"
            r"TimeoutError|Timeout|"
            r"URLError|HTTPError|WebDriverException)\b",
            message,
        )
    )
    if error_types:
        details = []
        for error_type in error_types:
            if (
                error_type == "ConnectionResetError"
                or error_type == "ConnectionReset"
                or "10054" in message
                or "connection reset" in message.lower()
            ):
                details.append(f"{error_type} (koneksi direset)")
            else:
                details.append(error_type)
        return ", ".join(details)
    return message[:140]


def _download_pdf_cached(url, referer=None):
    with _PDF_DOWNLOAD_CACHE_LOCK:
        download_lock = _PDF_DOWNLOAD_LOCKS.setdefault(url, Lock())

    try:
        with download_lock:
            now = time.monotonic()
            with _PDF_DOWNLOAD_CACHE_LOCK:
                cached = _PDF_DOWNLOAD_CACHE.get(url)
                if cached is not None and cached[0] > now:
                    _PDF_DOWNLOAD_CACHE.move_to_end(url)
                    if cached[2]:
                        raise RuntimeError(cached[2])
                    return cached[1]

                _PDF_DOWNLOAD_CACHE.pop(url, None)

            try:
                pdf_bytes = _download_pdf(url, referer=referer)
                if not pdf_bytes.startswith(b"%PDF"):
                    raise RuntimeError("respons unduhan bukan file PDF")
            except Exception as error:
                message = str(error).splitlines()[0][:240]
                with _PDF_DOWNLOAD_CACHE_LOCK:
                    _PDF_DOWNLOAD_CACHE[url] = (
                        time.monotonic() + _PDF_DOWNLOAD_FAILURE_TTL,
                        None,
                        message,
                    )
                    _PDF_DOWNLOAD_CACHE.move_to_end(url)
                    while (
                        len(_PDF_DOWNLOAD_CACHE)
                        > _PDF_DOWNLOAD_CACHE_MAX_ENTRIES
                    ):
                        _PDF_DOWNLOAD_CACHE.popitem(last=False)
                raise RuntimeError(message) from error

            with _PDF_DOWNLOAD_CACHE_LOCK:
                _PDF_DOWNLOAD_CACHE[url] = (
                    time.monotonic() + _PDF_DOWNLOAD_SUCCESS_TTL,
                    pdf_bytes,
                    None,
                )
                _PDF_DOWNLOAD_CACHE.move_to_end(url)
                while (
                    len(_PDF_DOWNLOAD_CACHE)
                    > _PDF_DOWNLOAD_CACHE_MAX_ENTRIES
                ):
                    _PDF_DOWNLOAD_CACHE.popitem(last=False)
            return pdf_bytes
    finally:
        with _PDF_DOWNLOAD_CACHE_LOCK:
            if _PDF_DOWNLOAD_LOCKS.get(url) is download_lock:
                _PDF_DOWNLOAD_LOCKS.pop(url, None)


def _is_bps_url(url):
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return host == "bps.go.id" or host.endswith(".bps.go.id")


def _is_pdf_url(url):
    parsed = urlsplit(str(url or ""))
    path = parsed.path.lower()
    return path.endswith(".pdf") or "download.php" in path


def _is_download_url(url):
    path = urlsplit(str(url or "")).path.lower()
    return (
        "download" in path
        or "attachment" in path
        or "/files/" in path
    )


def _find_pdf_links(html, page_url):
    source_html = html_unescape(str(html or "")).replace(r"\/", "/")
    soup = BeautifulSoup(source_html, "html.parser")
    urls = []

    for element in soup.find_all(["a", "iframe", "embed", "object"]):
        href = str(
            element.get("href")
            or element.get("src")
            or element.get("data")
            or ""
        ).strip()
        label = " ".join(
            (
                element.get_text(" ", strip=True),
                str(element.get("title") or ""),
                str(element.get("aria-label") or ""),
            )
        ).lower()
        indicated_pdf = (
            _is_pdf_url(href)
            or (
                _is_download_url(href)
                and any(
                    marker in label
                    for marker in ("pdf", "unduh", "download", "dokumen")
                )
            )
        )
        if not href or not indicated_pdf:
            continue

        resolved = urljoin(page_url, href)
        if resolved not in urls:
            urls.append(resolved)

    fallback = find_pdf_url(source_html)
    if fallback:
        resolved = urljoin(page_url, fallback)
        if resolved not in urls:
            urls.append(resolved)

    for match in re.findall(
        r"""(?:(?:https?:)?//[^"'<>\\\s]+|(?<![.\w])/(?!/)[^"'<>\\\s]+)
            (?:\.pdf|/download\.php\?[^"'<>\\\s]+)""",
        source_html,
        flags=re.IGNORECASE | re.VERBOSE,
    ):
        resolved = urljoin(page_url, match.rstrip("),;"))
        if resolved not in urls:
            urls.append(resolved)

    return urls


def _search_terms(text):
    ignored = {
        "ambil", "angka", "data", "indikator", "nilai", "tahun",
        "untuk", "dengan", "pada", "dari", "yang", "dan", "di",
        "provinsi", "prov", "kabupaten", "kab", "kota", "seluruh",
        "semua", "kode",
    }
    return [
        term
        for term in dict.fromkeys(re.findall(r"[a-z0-9]+", str(text or "").lower()))
        if len(term) > 2 and term not in ignored and not term.isdigit()
    ]


def _article_links(html, page_url, search_context, tahun):
    terms = _search_terms(search_context)
    articles = {}
    soup = BeautifulSoup(html or "", "html.parser")
    current_path = urlsplit(page_url).path

    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, str(anchor.get("href") or "").strip())
        parsed = urlsplit(href)
        if (
            not _is_bps_url(href)
            or parsed.path == current_path
            or parsed.path.lower().endswith(
                (".pdf", ".xlsx", ".xls", ".csv", ".zip")
            )
            or parsed.path.lower().endswith(
                (".js", ".css", ".png", ".jpg", ".jpeg", ".svg")
            )
        ):
            continue

        title = anchor.get_text(" ", strip=True)
        searchable = f"{title} {parsed.path}".lower()
        score = sum(
            bool(re.search(rf"(?<!\w){re.escape(term)}(?!\w)", searchable))
            for term in terms
        )
        if terms and score < min(2, len(terms)):
            continue
        if not title and score == 0:
            continue

        articles[href] = {
            "url": href,
            "title": title or parsed.path.rsplit("/", 1)[-1],
            "score": score,
            "year_match": int(str(tahun) in searchable),
        }

    return sorted(
        articles.values(),
        key=lambda item: (item["year_match"], item["score"]),
        reverse=True,
    )


def _page_with_number(url, page_number):
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    query["page"] = [str(page_number)]
    return urlunsplit(parsed._replace(query=urlencode(query, doseq=True)))


def _render_page(url, search_context, tahun):
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait

    options = webdriver.ChromeOptions()
    options.page_load_strategy = "eager"
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")

    driver = webdriver.Chrome(options=options)
    articles = {}
    rendered_html = ""

    try:
        driver.set_page_load_timeout(30)
        page_count = 5 if _is_listing_url(url) else 1
        for page_number in range(1, page_count + 1):
            require_pdf = page_count == 1
            page_url = (
                _page_with_number(url, page_number)
                if page_count > 1
                else url
            )
            try:
                driver.get(page_url)
            except TimeoutException:
                if not _rendered_page_has_targets(
                    driver.page_source or "",
                    page_url,
                    search_context,
                    tahun,
                    require_pdf=require_pdf,
                ):
                    if page_number > 1:
                        break
                    raise

            content_wait = 20 if page_number == 1 else 5
            try:
                WebDriverWait(driver, content_wait).until(
                    lambda browser: _rendered_page_has_targets(
                        browser.page_source or "",
                        page_url,
                        search_context,
                        tahun,
                        require_pdf=require_pdf,
                    )
                )
            except TimeoutException:
                if page_number == 1:
                    raise RuntimeError(
                        "Halaman BPS selesai dibuka tetapi hasil "
                        "pencarian atau tautan unduhan tidak termuat."
                    )
                break

            page_html = driver.page_source or ""
            rendered_html = page_html

            if page_count == 1:
                break

            found = _article_links(
                page_html, page_url, search_context, tahun
            )
            old_count = len(articles)
            for article in found:
                articles.setdefault(article["url"], article)
            if len(articles) == old_count:
                break
    finally:
        driver.quit()

    ranked_articles = sorted(
        articles.values(),
        key=lambda item: (item["year_match"], item["score"]),
        reverse=True,
    )
    return rendered_html, ranked_articles


def _is_listing_url(url):
    path = urlsplit(url).path.lower()
    return any(
        marker in path
        for marker in (
            "/pressrelease",
            "/publication",
            "/publikasi",
            "/news",
            "/berita",
            "/subject",
            "/artikel",
        )
    ) and not path.endswith((".html", ".htm"))


def _looks_like_dynamic_page(text):
    lowered = str(text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "__next_data__",
            "self.__next_f",
            "_next/static",
            'id="__next"',
            "data-reactroot",
            "window.__initial_state__",
        )
    )


def _rendered_page_has_targets(
    html,
    url,
    search_context,
    tahun,
    require_pdf=False,
):
    pdf_links = _find_pdf_links(html, url)
    if pdf_links:
        return True
    if require_pdf:
        return False
    return bool(_article_links(html, url, search_context, tahun))


def _find_pdf_urls(url, html, search_context, tahun, page_kind=None):
    if _is_pdf_url(url):
        _PDF_REFERER_CACHE.setdefault(url, url)
        return [url]

    is_listing = _is_listing_url(url)
    pdf_urls = _find_pdf_links(html, url)
    for pdf_url in pdf_urls:
        _PDF_REFERER_CACHE.setdefault(pdf_url, url)
    if pdf_urls and not is_listing:
        return pdf_urls

    article_map = {
        article["url"]: article
        for article in _article_links(html, url, search_context, tahun)
    }

    if is_listing:
        for page_number in range(2, _MAX_LISTING_PAGES + 1):
            page_url = _page_with_number(url, page_number)
            try:
                page_html = download_html(
                    page_url,
                    timeout=15,
                    tag="BPS-PDF",
                )
            except Exception as error:
                print(
                    "    [BPS-PDF] Halaman pencarian "
                    f"{page_number} gagal diproses: "
                    f"{str(error).splitlines()[0][:160]}"
                )
                break

            page_pdfs = _find_pdf_links(page_html, page_url)
            pdf_urls.extend(page_pdfs)
            for pdf_url in page_pdfs:
                _PDF_REFERER_CACHE.setdefault(pdf_url, page_url)
            page_articles = _article_links(
                page_html,
                page_url,
                search_context,
                tahun,
            )
            if not page_articles and not page_pdfs:
                break
            for article in page_articles:
                article_map.setdefault(article["url"], article)

    articles = sorted(
        article_map.values(),
        key=lambda item: (item["year_match"], item["score"]),
        reverse=True,
    )

    if (
        not articles
        and not pdf_urls
        and (
            is_listing
            or page_kind == "spa"
            or _looks_like_dynamic_page(html)
        )
    ):
        rendered_html, rendered_articles = _render_page(
            url, search_context, tahun
        )
        rendered_pdfs = _find_pdf_links(rendered_html, url)
        pdf_urls.extend(rendered_pdfs)
        for pdf_url in rendered_pdfs:
            _PDF_REFERER_CACHE.setdefault(pdf_url, url)
        rendered_articles = rendered_articles or _article_links(
            rendered_html,
            url,
            search_context,
            tahun,
        )
        articles = sorted(
            rendered_articles,
            key=lambda item: (item["year_match"], item["score"]),
            reverse=True,
        )

    # Follow a small set of same-site pages whose title/path matches the
    # requested indicator; this works for publications, articles, and
    # subject pages rather than only the press-release section.
    for article in articles[:_MAX_ARTICLE_PAGES]:
        try:
            article_html = download_html(
                article["url"],
                tag="BPS-PDF",
            )
            article_pdfs = _find_pdf_links(article_html, article["url"])
            if not article_pdfs and _looks_like_dynamic_page(article_html):
                article_html, _ = _render_page(
                    article["url"], search_context, tahun
                )
                article_pdfs = _find_pdf_links(
                    article_html, article["url"]
                )
            for pdf_url in article_pdfs:
                _PDF_REFERER_CACHE[pdf_url] = article["url"]
            pdf_urls.extend(article_pdfs)
        except Exception as error:
            print(
                "    [BPS-PDF] Halaman terkait gagal diproses: "
                f"{str(error).splitlines()[0][:200]}"
            )

    return list(dict.fromkeys(pdf_urls))


def _extract_from_downloaded_pdf(pdf_url, pdf_bytes, instruction, tahun):
    payload = SourcePayload(
        url=pdf_url,
        kind="pdf",
        content=pdf_bytes,
        content_type="application/pdf",
        final_url=pdf_url,
    )
    return extract_pdf(payload, instruction, int(tahun))


def _merge_pdf_results(results, target_regions):
    region_values = {}
    evidence = []
    confidences = []
    saw_review = False

    for pdf_url, result in results:
        evidence.append(f"pdf_url={pdf_url}")
        evidence.extend(result.evidence)
        confidences.append(result.confidence)
        saw_review = saw_review or result.status == "REVIEW"

        if isinstance(result.value, dict):
            for region, value in result.value.items():
                if region != "__main__" and value is not None:
                    region_values.setdefault(region, value)
        elif result.value is not None and len(target_regions) == 1:
            return result

    if not region_values:
        return None

    missing = [
        region for region in target_regions
        if region not in region_values
    ]
    status = (
        "REVIEW"
        if saw_review or missing
        else "SUCCESS"
    )
    message = (
        f"PDF BPS terbaca untuk {len(region_values)} wilayah."
    )
    if missing:
        message += (
            f" Belum ditemukan {len(missing)} dari "
            f"{len(target_regions)} wilayah yang diminta."
        )

    return ExtractionResult(
        status=status,
        value=region_values,
        confidence=min(confidences, default=0.0),
        source_type="bps_pdf",
        message=message,
        evidence=evidence[:30],
    )


def try_bps_pdf(
    url,
    tahun,
    description=None,
    wilayah=None,
    nama_indikator=None,
    page_html=None,
    page_kind=None,
):
    """Find a PDF linked from a BPS page and use the shared PDF extractor."""
    source_url = str(url or "").strip()
    if not _is_bps_url(source_url):
        return None

    search_context = " ".join(
        part
        for part in (str(nama_indikator or ""), str(description or ""))
        if part
    )
    year = int(tahun)
    cache_key = (
        source_url,
        search_context,
    )
    year_cache_key = (
        source_url,
        year,
        search_context,
        str(wilayah or "").strip().lower(),
    )
    cached = _RESULT_CACHE.get(year_cache_key)
    if cached is not None:
        return copy.deepcopy(cached)

    instruction = parse_instruction(
        search_context,
        default_wilayah=wilayah,
    )

    try:
        if page_html is None and not _is_pdf_url(source_url):
            page_html = download_html(source_url, tag="BPS-PDF")

        pdf_urls = _PDF_URL_CACHE.get(cache_key)
        if pdf_urls is None:
            pdf_urls = _find_pdf_urls(
                source_url,
                page_html or "",
                search_context,
                year,
                page_kind,
            )
            if pdf_urls:
                _PDF_URL_CACHE[cache_key] = tuple(pdf_urls)
    except Exception as error:
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_pdf",
            message=f"Gagal menemukan PDF BPS: {str(error)[:200]}",
            evidence=["pdf_discovery=failed"],
        )

    if not pdf_urls:
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_pdf",
            message="Tidak menemukan tautan PDF BPS pada sumber.",
            evidence=["pdf_candidates=0"],
        )

    results = []
    errors = []
    candidates = pdf_urls[:10]
    for candidate_index, pdf_url in enumerate(candidates, start=1):
        host = urlsplit(pdf_url).hostname or "host tidak diketahui"
        print(
            f"    [BPS-PDF] Mengunduh PDF "
            f"{candidate_index}/{len(candidates)} dari {host}"
        )
        try:
            pdf_bytes = _download_pdf_cached(
                pdf_url,
                referer=_PDF_REFERER_CACHE.get(pdf_url, source_url),
            )
            print(
                f"    [BPS-PDF] PDF {candidate_index}: "
                f"{len(pdf_bytes)} byte; mulai ekstraksi"
            )
            result = _extract_from_downloaded_pdf(
                pdf_url,
                pdf_bytes,
                instruction,
                year,
            )
            print(
                f"    [BPS-PDF] PDF {candidate_index}: "
                f"status={result.status}, "
                f"confidence={result.confidence:.2f}, "
                f"message={result.message[:160]}, "
                f"evidence={','.join(result.evidence[:8])}"
            )
            if result.value is not None:
                results.append((pdf_url, result))
                if (
                    len(instruction.target_wilayahs) == 1
                    and result.status == "SUCCESS"
                    and result.confidence >= 0.85
                ):
                    break
                partial = _merge_pdf_results(
                    results,
                    instruction.target_wilayahs,
                )
                if partial is not None and partial.status == "SUCCESS":
                    break
            else:
                errors.append(
                    f"PDF {candidate_index}: {result.message[:180]}; "
                    f"evidence={','.join(result.evidence[:8])}"
                )
        except Exception as error:
            detail = str(error).splitlines()[0][:180]
            errors.append(f"PDF {candidate_index}: {detail}")
            print(
                f"    [BPS-PDF] PDF {candidate_index} gagal: {detail}"
            )

    merged = _merge_pdf_results(results, instruction.target_wilayahs)
    if merged is not None:
        _RESULT_CACHE[year_cache_key] = copy.deepcopy(merged)
        return merged

    if results:
        errors.append(
            "PDF berhasil diproses tetapi hasil tidak dapat dipetakan "
            "ke wilayah yang diminta."
        )

    failure = ExtractionResult(
        status="NEED_PARSER",
        value=None,
        confidence=0.0,
        source_type="bps_pdf",
        message=(
            "PDF ditemukan tetapi belum dapat mengekstrak data yang diminta. "
            + "; ".join(errors[:3])
        ),
        evidence=[f"pdf_candidates={len(pdf_urls)}"],
    )
    _RESULT_CACHE[year_cache_key] = copy.deepcopy(failure)
    return failure


__all__ = ["try_bps_pdf"]
