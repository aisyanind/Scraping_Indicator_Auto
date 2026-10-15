import base64
import json
import os
import threading
import time
from urllib.parse import urlsplit

from selenium import webdriver
from selenium.common.exceptions import TimeoutException


# Pengunduh PDF lewat Chrome (headed) untuk menembus proteksi
# Cloudflare yang memblokir requests biasa (403).
#
# Fakta lapangan:
#   - requests / curl / Chrome headless -> 403 (managed challenge)
#   - Chrome headed + navigator.webdriver disembunyikan -> 200 PDF

BROWSER_PDF_ATTEMPTS = 4
BROWSER_POLL_SECONDS = 25
BROWSER_RETRY_BACKOFF = 4
PAGE_LOAD_TIMEOUT = 60

MIN_PDF_BYTES = 1024

_PROFILE_ROOT = os.path.join(
    os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
    "ScrapingIndikatorAuto",
)

_BROWSER_LOCK = threading.Lock()


def _chrome_options(profile_dir=None):
    options = webdriver.ChromeOptions()
    options.add_argument("--window-size=1200,900")
    options.add_argument("--window-position=4000,4000")
    options.add_argument("--log-level=3")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option(
        "excludeSwitches",
        ["enable-logging"],
    )
    options.set_capability(
        "goog:loggingPrefs",
        {"performance": "ALL"},
    )

    if profile_dir:
        os.makedirs(profile_dir, exist_ok=True)
        options.add_argument(f"--user-data-dir={profile_dir}")

    return options


def _create_driver(use_profile=True):
    profile_dir = os.path.join(_PROFILE_ROOT, "chrome_profile_bappenas")

    if use_profile:
        try:
            return webdriver.Chrome(options=_chrome_options(profile_dir))
        except Exception as error:
            print(
                "    [BROWSER] Profil Chrome permanen gagal "
                f"({type(error).__name__}); pakai profil sementara."
            )

    return webdriver.Chrome(options=_chrome_options(None))


def _hide_automation(driver):
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": (
                    "Object.defineProperty(navigator,'webdriver',"
                    "{get:()=>undefined});"
                ),
            },
        )
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass


def _drain_performance(driver):
    try:
        return driver.get_log("performance")
    except Exception:
        return []


def _iter_documents(driver):
    documents = []

    for entry in _drain_performance(driver):
        try:
            message = json.loads(entry["message"])["message"]
        except Exception:
            continue

        if message.get("method") != "Network.responseReceived":
            continue

        params = message.get("params", {})

        if params.get("type") != "Document":
            continue

        response = params.get("response", {})
        documents.append(
            {
                "request_id": params.get("requestId"),
                "status": int(response.get("status") or 0),
                "mime": str(response.get("mimeType") or "").lower(),
                "url": str(response.get("url") or ""),
            }
        )

    return documents


def _is_valid_pdf(data):
    return (
        isinstance(data, (bytes, bytearray))
        and len(data) >= MIN_PDF_BYTES
        and bytes(data[:4]) == b"%PDF"
    )


_FETCH_IN_PAGE_JS = """
  const resp = await fetch(arguments[0], {credentials: 'include'});
  if (!resp.ok) return 'ERR:' + resp.status;
  const buf = await resp.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 8192) {
    bin += String.fromCharCode.apply(
      null,
      bytes.subarray(i, i + 8192)
    );
  }
  return btoa(bin);
"""


def _fetch_in_page(driver, url):
    try:
        payload = driver.execute_script(_FETCH_IN_PAGE_JS, url)
    except Exception:
        return None

    if not payload or payload.startswith("ERR:"):
        return None

    try:
        return base64.b64decode(payload)
    except Exception:
        return None


def _wait_loading_finished(driver, request_id, timeout=10):
    deadline = time.time() + timeout

    while time.time() < deadline:
        for entry in _drain_performance(driver):
            try:
                message = json.loads(entry["message"])["message"]
            except Exception:
                continue

            if message.get("method") != "Network.loadingFinished":
                continue

            params = message.get("params", {})

            if params.get("requestId") == request_id:
                return True

        time.sleep(0.4)

    return False


def _body_from_cdp(driver, request_id):
    if not request_id:
        return None

    if not _wait_loading_finished(driver, request_id):
        return None

    try:
        body = driver.execute_cdp_cmd(
            "Network.getResponseBody",
            {"requestId": request_id},
        )
    except Exception:
        return None

    raw = body.get("body", "")

    try:
        if body.get("base64Encoded"):
            data = base64.b64decode(raw)
        else:
            data = raw.encode("utf-8")
    except Exception:
        return None

    return data if _is_valid_pdf(data) else None


def _resolve_pdf(driver, url, document):
    if document["status"] != 200 or "pdf" not in document["mime"]:
        return None

    data = _fetch_in_page(driver, url)

    if _is_valid_pdf(data):
        return data

    data = _body_from_cdp(driver, document.get("request_id"))

    if _is_valid_pdf(data):
        return data

    return None


def _download_once(driver, url, tag):
    target_netloc = urlsplit(url).netloc.lower()

    _drain_performance(driver)

    try:
        driver.get(url)
    except TimeoutException:
        pass
    except Exception:
        return None

    deadline = time.time() + BROWSER_POLL_SECONDS
    reloaded = False
    saw_blocked = False

    while time.time() < deadline:
        for document in _iter_documents(driver):
            doc_netloc = urlsplit(document["url"]).netloc.lower()

            if doc_netloc != target_netloc:
                continue

            data = _resolve_pdf(driver, url, document)

            if data:
                return data

            if document["status"] in {401, 403, 429}:
                saw_blocked = True

        if saw_blocked and not reloaded:
            title = str(driver.title or "")

            if "just a moment" not in title.lower() and title.strip():
                reloaded = True
                _drain_performance(driver)

                try:
                    driver.get(url)
                except Exception:
                    pass

        time.sleep(0.8)

    return None


def download_pdf_via_browser(
    url,
    tag="BROWSER",
    attempts=BROWSER_PDF_ATTEMPTS,
):
    print(f"    [{tag}] Mengunduh via Chrome: {url}")

    with _BROWSER_LOCK:
        driver = _create_driver()

        try:
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            _hide_automation(driver)

            for attempt in range(1, attempts + 1):
                data = _download_once(driver, url, tag)

                if data:
                    print(
                        f"    [{tag}] Unduhan via Chrome berhasil "
                        f"({len(data)} byte)."
                    )
                    return bytes(data)

                if attempt < attempts:
                    wait = BROWSER_RETRY_BACKOFF * attempt
                    print(
                        f"    [{tag}] Percobaan {attempt}/{attempts} "
                        f"gagal; ulangi dalam {wait}s..."
                    )
                    time.sleep(wait)

            raise RuntimeError(
                "SOURCE_BLOCKED: "
                f"Chrome tidak bisa mengunduh {url} "
                f"setelah {attempts} percobaan "
                "(terblokir proteksi Cloudflare)."
            )

        finally:
            try:
                driver.quit()
            except Exception:
                pass


def render_html_via_browser(url, tag="BROWSER", wait_seconds=6):
    print(f"    [{tag}] Membuka halaman via Chrome: {url}")

    with _BROWSER_LOCK:
        driver = _create_driver()

        try:
            driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
            _hide_automation(driver)

            try:
                driver.get(url)
            except TimeoutException:
                pass

            deadline = time.time() + 20

            while time.time() < deadline:
                title = str(driver.title or "")

                if "just a moment" not in title.lower() and title.strip():
                    break

                time.sleep(1)

            time.sleep(wait_seconds)

            return driver.page_source or ""

        finally:
            try:
                driver.quit()
            except Exception:
                pass


__all__ = [
    "download_pdf_via_browser",
    "render_html_via_browser",
]
