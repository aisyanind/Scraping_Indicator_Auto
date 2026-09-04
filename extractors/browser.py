from io import StringIO
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
