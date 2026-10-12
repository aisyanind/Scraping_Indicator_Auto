from io import StringIO
import base64
import json
import time

import pandas as pd
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait

from engine.tabular import extract_from_dataframes
from models import ExtractionResult


def _chrome_options():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")
    options.set_capability(
        "goog:loggingPrefs",
        {"performance": "ALL"},
    )
    return options


def render_page(url, wait_seconds=5):
    """
    Render halaman dengan Chrome headless, return HTML hasil render.
    Dipakai untuk halaman SPA/dashboard yang butuh JS.
    """
    options = _chrome_options()
    driver = webdriver.Chrome(options=options)

    try:
        driver.set_page_load_timeout(90)
        driver.get(url)

        WebDriverWait(driver, 45).until(
            lambda d: d.execute_script("return document.readyState")
            == "complete"
        )
        time.sleep(wait_seconds)

        return driver.page_source or ""
    finally:
        driver.quit()


def render_page_capture_network(url, wait_seconds=5):
    """Render halaman dan kembalikan respons JSON dari fetch/XHR browser."""
    options = _chrome_options()
    driver = webdriver.Chrome(options=options)
    responses = []
    seen_urls = set()

    try:
        driver.execute_cdp_cmd("Network.enable", {})
        driver.set_page_load_timeout(90)
        driver.get(url)

        WebDriverWait(driver, 45).until(
            lambda d: d.execute_script("return document.readyState")
            == "complete"
        )
        time.sleep(wait_seconds)

        for entry in driver.get_log("performance"):
            try:
                message = json.loads(entry["message"])["message"]
                if message.get("method") != "Network.responseReceived":
                    continue

                params = message.get("params", {})
                response = params.get("response", {})
                resource_type = params.get("type", "").lower()
                content_type = response.get("mimeType", "").lower()
                response_url = response.get("url", "")

                if resource_type not in {"fetch", "xhr"}:
                    continue
                if not any(
                    marker in content_type
                    for marker in (
                        "json", "javascript", "pdf", "image/png", "image/jpeg",
                        "image/tiff",
                    )
                ):
                    continue
                if not response_url or response_url in seen_urls:
                    continue

                body = driver.execute_cdp_cmd(
                    "Network.getResponseBody",
                    {"requestId": params.get("requestId")},
                )
                raw_body = body.get("body", "")
                if body.get("base64Encoded"):
                    raw_content = base64.b64decode(raw_body)
                else:
                    raw_content = raw_body.encode("utf-8")

                if not raw_content:
                    continue

                seen_urls.add(response_url)
                responses.append((response_url, raw_content, content_type))
            except Exception:
                # Be tolerant of browser-internal responses that disappear
                # before Network.getResponseBody is called.
                continue

        try:
            screenshot_data = driver.execute_cdp_cmd(
                "Page.captureScreenshot",
                {
                    "format": "png",
                    "captureBeyondViewport": True,
                },
            )
            screenshot = base64.b64decode(screenshot_data["data"])
        except Exception:
            screenshot = driver.get_screenshot_as_png()

        return driver.page_source or "", responses, screenshot
    finally:
        driver.quit()


def extract_browser(url, instruction, tahun):
    html = render_page(url)

    try:
        frames = pd.read_html(StringIO(html))
    except Exception:
        frames = []

    result = extract_from_dataframes(
        frames,
        instruction,
        tahun,
        source_type="browser",
    )

    if result.status == "SUCCESS":
        return result

    return ExtractionResult(
        status="NEED_PARSER",
        source_type="browser",
        message=(
            "Dashboard/browser berhasil dibuka, tetapi struktur "
            "tidak bisa diekstrak generik. Perlu source adapter."
        ),
    )
