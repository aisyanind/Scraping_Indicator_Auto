from io import StringIO
import base64
import json
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from engine.tabular import extract_from_dataframes
from models import ExtractionResult


BROWSER_RETRIES = 2


def _chrome_options():
    options = webdriver.ChromeOptions()
    options.page_load_strategy = "eager"
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--no-first-run")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--log-level=3")
    options.set_capability(
        "goog:loggingPrefs",
        {"performance": "ALL"},
    )
    return options


def _wait_document_ready(driver, timeout=45):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState")
        in {"interactive", "complete"}
    )


def _safe_get(driver, url):
    """
    Navigasi toleran terhadap page-load timeout. Jika Chrome masih hidup,
    halaman boleh diproses setelah DOM interactive meskipun event load lambat.
    """
    try:
        driver.get(url)
    except TimeoutException:
        # Timeout navigasi tidak selalu berarti sesi Chrome mati.
        state = driver.execute_script("return document.readyState")
        if state not in {"interactive", "complete"}:
            _wait_document_ready(driver, timeout=20)
        return

    _wait_document_ready(driver, timeout=45)


def _remove_year_param(url):
    parts = urlsplit(url)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(
                parts.query,
                keep_blank_values=True,
            )
            if key.lower() != "year"
        ]
    )
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            query,
            parts.fragment,
        )
    )


def _close_simple_overlay(driver):
    try:
        for button in driver.find_elements(By.TAG_NAME, "button"):
            if (
                button.is_displayed()
                and button.text.strip().lower() in {"tutup", "close"}
            ):
                button.click()
                return
    except WebDriverException:
        return


def _visible_table_text(driver):
    return driver.execute_script(
        """
        const table = document.querySelector('table');
        if (table) return table.innerText || '';
        return document.body ? document.body.innerText.slice(0, 5000) : '';
        """
    ) or ""


def _page_year_evidence(driver):
    """Baca tahun yang benar-benar tampil pada judul dan header tabel."""
    try:
        return driver.execute_script(
            """
            const heading = Array.from(document.querySelectorAll('h1'))
              .map(el => (el.innerText || el.textContent || '').trim())
              .filter(Boolean)
              .join(' | ');
            const table = document.querySelector('#data-table') ||
                          document.querySelector('table');
            const tableHeader = table
              ? (table.querySelector('thead')?.innerText || '').trim()
              : '';
            const tablePreview = table
              ? (table.innerText || '').slice(0, 1600)
              : '';
            return {
              heading: heading,
              table_header: tableHeader,
              table_preview: tablePreview
            };
            """
        ) or {}
    except WebDriverException:
        return {}


def _page_header_has_year(driver, year):
    evidence = _page_year_evidence(driver)
    text = " ".join(
        str(evidence.get(key) or "")
        for key in ("heading", "table_header", "table_preview")
    )
    return bool(
        re.search(
            rf"(?<!\d){re.escape(str(year))}(?!\d)",
            text,
        )
    )


def _find_react_select_year_control(driver):
    """
    Temukan React Select tahun BPS.

    Input React Select memang sengaja dibuat opacity: 0. Karena itu,
    visibilitas harus diperiksa pada wrapper/control yang terlihat,
    bukan pada input[role=combobox] itu sendiri.
    """
    try:
        inputs = driver.find_elements(
            By.CSS_SELECTOR,
            'input[role="combobox"]',
        )
    except WebDriverException:
        return None

    for input_element in inputs:
        try:
            container = driver.execute_script(
                """
                return arguments[0].closest('div[class*="container"]');
                """,
                input_element,
            )
            if container is None or not container.is_displayed():
                continue

            control = driver.execute_script(
                """
                return arguments[0].querySelector('div[class*="-control"]');
                """,
                container,
            )
            if control is None or not control.is_displayed():
                continue

            selected = driver.execute_script(
                """
                const value = arguments[0].querySelector(
                  '[class*="singleValue"]'
                );
                return (value?.innerText || value?.textContent || '').trim();
                """,
                control,
            )
            if re.fullmatch(r"(?:19|20)\d{2}", str(selected or "")):
                return input_element, control, str(selected)
        except WebDriverException:
            # React dapat mengganti node saat menu dibuka; coba input lain.
            continue

    return None


def _find_visible_year_option(driver, year):
    """Temukan opsi React Select yang teksnya sama persis dengan tahun."""
    try:
        options = driver.find_elements(
            By.CSS_SELECTOR,
            '[role="option"]',
        )
    except WebDriverException:
        return None

    for option in options:
        try:
            if not option.is_displayed():
                continue
            text = re.sub(r"\s+", " ", option.text or "").strip()
            if text == str(year):
                return option
        except WebDriverException:
            continue

    return None


def _click_safely(driver, element):
    """Klik elemen; gunakan native DOM click bila Selenium terhalang overlay."""
    try:
        element.click()
    except WebDriverException:
        driver.execute_script("arguments[0].click();", element)


def _wait_for_page_year(driver, year, timeout=30, context="tabel BPS"):
    try:
        WebDriverWait(
            driver,
            timeout,
            poll_frequency=0.5,
        ).until(lambda d: _page_header_has_year(d, year))
    except TimeoutException as error:
        evidence = _page_year_evidence(driver)
        heading = str(evidence.get("heading") or "").strip()
        table_header = str(
            evidence.get("table_header") or ""
        ).strip()
        raise TimeoutException(
            f"Tahun target {year} belum terverifikasi pada {context}. "
            f"Judul={heading[:220]!r}; "
            f"header_tabel={table_header[:220]!r}."
        ) from error


def _wait_for_react_select_year(driver, year, timeout=30):
    def selected_and_header_match(current_driver):
        state = _find_react_select_year_control(current_driver)
        if state is None:
            return False
        _input, _control, selected = state
        return (
            selected == str(year)
            and _page_header_has_year(current_driver, year)
        )

    try:
        WebDriverWait(
            driver,
            timeout,
            poll_frequency=0.5,
        ).until(selected_and_header_match)
    except TimeoutException as error:
        state = _find_react_select_year_control(driver)
        selected = state[2] if state else "tidak terbaca"
        evidence = _page_year_evidence(driver)
        heading = str(evidence.get("heading") or "").strip()
        table_header = str(
            evidence.get("table_header") or ""
        ).strip()
        raise TimeoutException(
            f"Pemilihan tahun React Select tidak terverifikasi. "
            f"Target={year}; dropdown={selected}; "
            f"judul={heading[:220]!r}; "
            f"header_tabel={table_header[:220]!r}."
        ) from error


def _select_react_select_year(driver, year, wait_seconds=3):
    """Pilih tahun pada React Select BPS dan verifikasi perubahan tabel."""
    state = _find_react_select_year_control(driver)
    if state is None:
        return None

    input_element, control, selected = state
    if (
        selected == str(year)
        and _page_header_has_year(driver, year)
    ):
        return "react_select_already_selected"

    driver.execute_script(
        "arguments[0].scrollIntoView({block: 'center'});",
        control,
    )
    _click_safely(driver, control)

    option = _find_visible_year_option(driver, year)

    # Jika opsi belum terlihat, gunakan input internal React Select
    # untuk memfilter daftar. Input dapat ber-opacity: 0, tetapi tetap
    # menerima keyboard ketika control memperoleh fokus.
    if option is None:
        try:
            driver.execute_script(
                "arguments[0].focus();",
                input_element,
            )
            active = driver.switch_to.active_element
            active.send_keys(Keys.CONTROL, "a")
            active.send_keys(str(year))
        except WebDriverException:
            # Alternatif: kirim keyboard langsung ke input combobox.
            input_element.send_keys(Keys.CONTROL, "a")
            input_element.send_keys(str(year))

        try:
            option = WebDriverWait(
                driver,
                max(5, wait_seconds * 2),
                poll_frequency=0.25,
            ).until(
                lambda d: _find_visible_year_option(d, year)
            )
        except TimeoutException as error:
            raise TimeoutException(
                f"Opsi tahun {year} tidak ditemukan pada React Select BPS."
            ) from error

    _click_safely(driver, option)
    _wait_for_react_select_year(driver, year, timeout=30)
    return "react_select"


def _select_year_in_browser(driver, url, tahun, wait_seconds=3):
    """
    Pilih tahun melalui kontrol halaman. Mendukung tombol tahun dan select.
    Jika kontrol tidak ditemukan, URL asli dengan ?year=... dicoba kembali.
    """
    year = str(tahun)
    base_url = _remove_year_param(url)

    if base_url != getattr(driver, "current_url", ""):
        _safe_get(driver, base_url)
        time.sleep(min(wait_seconds, 2))

    _close_simple_overlay(driver)
    before_text = _visible_table_text(driver)

    # Pola 1: dropdown <select><option>2025</option></select>.
    try:
        for select_element in driver.find_elements(By.TAG_NAME, "select"):
            options = select_element.find_elements(By.TAG_NAME, "option")
            for option in options:
                option_text = (option.text or "").strip()
                option_value = (option.get_attribute("value") or "").strip()
                if option_text == year or option_value == year:
                    selected_value = option_value or year
                    driver.execute_script(
                        """
                        const el = arguments[0];
                        el.value = arguments[1];
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        """,
                        select_element,
                        selected_value,
                    )
                    _wait_for_page_year(
                        driver,
                        year,
                        timeout=max(10, wait_seconds * 6),
                        context="header tabel setelah pemilihan <select>",
                    )
                    return "select"
    except WebDriverException:
        raise

    # Pola 2: React Select yang dipakai halaman tabel statistik BPS.
    # Input combobox-nya transparan (opacity: 0), sehingga yang diklik
    # adalah wrapper control yang terlihat.
    react_select_method = _select_react_select_year(
        driver,
        year,
        wait_seconds=wait_seconds,
    )
    if react_select_method:
        return react_select_method

    # Pola 3: tombol pilihan tahun.
    try:
        year_buttons = driver.find_elements(
            By.XPATH,
            f"//*[self::button or @role='button'][normalize-space(.)='{year}']",
        )
        for button in year_buttons:
            if not button.is_displayed():
                continue

            classes = button.get_attribute("class") or ""
            aria_pressed = (
                button.get_attribute("aria-pressed") or ""
            ).lower()
            is_selected = (
                "bg-main-light" in classes
                or "selected" in classes.lower()
                or aria_pressed == "true"
            )
            if not is_selected:
                driver.execute_script("arguments[0].click();", button)

            _wait_for_page_year(
                driver,
                year,
                timeout=max(10, wait_seconds * 6),
                context="header tabel setelah pemilihan tombol tahun",
            )
            return "button"
    except WebDriverException:
        raise

    # Pola 4: coba query year hanya sebagai fallback. Query tidak dianggap
    # berhasil sampai judul/header tabel benar-benar menampilkan tahun target.
    if url != getattr(driver, "current_url", ""):
        _safe_get(driver, url)
        time.sleep(wait_seconds)
        _wait_for_page_year(
            driver,
            year,
            timeout=max(10, wait_seconds * 6),
            context="header tabel setelah fallback query year",
        )
        return "query"

    after_text = _visible_table_text(driver)
    if (
        after_text != before_text
        and _page_header_has_year(driver, year)
    ):
        return "already_loaded"

    evidence = _page_year_evidence(driver)
    raise TimeoutException(
        f"Kontrol tahun tidak ditemukan atau tahun target {year} belum "
        "terpilih. "
        f"Judul={str(evidence.get('heading') or '')[:220]!r}; "
        f"header_tabel={str(evidence.get('table_header') or '')[:220]!r}."
    )


def _run_with_chrome(operation, label):
    errors = []

    for attempt in range(1, BROWSER_RETRIES + 1):
        driver = None
        try:
            driver = webdriver.Chrome(options=_chrome_options())
            return operation(driver)
        except (TimeoutException, WebDriverException) as error:
            detail = (
                f"{error.__class__.__name__}: "
                f"{str(error).splitlines()[0][:180]}"
            )
            errors.append(detail)
            if attempt < BROWSER_RETRIES:
                print(
                    f"    [BROWSER] {label} gagal pada percobaan "
                    f"{attempt}/{BROWSER_RETRIES}: {detail}. "
                    "Membuat sesi Chrome baru..."
                )
                time.sleep(attempt)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    raise RuntimeError(
        f"{label} gagal setelah {BROWSER_RETRIES} sesi Chrome: "
        + " | ".join(errors)
    )


def render_page(url, wait_seconds=5, select_year=None):
    """
    Render halaman dengan Chrome headless, return HTML hasil render.
    Dipakai untuk halaman SPA/dashboard yang butuh JavaScript.
    Sesi Chrome dibuat ulang jika renderer menutup koneksi.
    """
    def operation(driver):
        driver.set_page_load_timeout(75)
        _safe_get(driver, url)
        time.sleep(wait_seconds)

        if select_year is not None:
            _select_year_in_browser(
                driver,
                url,
                select_year,
                wait_seconds=min(wait_seconds, 4),
            )

        return driver.page_source or ""

    return _run_with_chrome(operation, "Render halaman")


def _is_supported_network_response(resource_type, content_type, response_url):
    resource_type = str(resource_type or "").lower()
    content_type = str(content_type or "").lower()
    response_url = str(response_url or "").lower()

    if resource_type not in {"fetch", "xhr"}:
        return False

    supported_types = (
        "json",
        "javascript",
        "pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "text/x-component",
        "text/csv",
        "application/csv",
        "spreadsheetml",
        "application/vnd.ms-excel",
    )
    if any(marker in content_type for marker in supported_types):
        return True

    # Server Action tertentu mengirim text/plain; batasi ke endpoint tabel BPS.
    if (
        "text/plain" in content_type
        and "bps.go.id" in response_url
        and "statistics-table" in response_url
    ):
        return True

    # File ekspor kadang menggunakan application/octet-stream.
    if (
        "octet-stream" in content_type
        and any(ext in response_url for ext in (".csv", ".xlsx", ".xls"))
    ):
        return True

    return False


def _capture_network_responses(driver, entries):
    responses = []
    seen_request_ids = set()

    for entry in entries:
        try:
            message = json.loads(entry["message"])["message"]
            if message.get("method") != "Network.responseReceived":
                continue

            params = message.get("params", {})
            response = params.get("response", {})
            resource_type = params.get("type", "")
            content_type = response.get("mimeType", "").lower()
            response_url = response.get("url", "")

            if not _is_supported_network_response(
                resource_type,
                content_type,
                response_url,
            ):
                continue

            request_id = params.get("requestId")
            if not request_id or request_id in seen_request_ids:
                continue

            body = driver.execute_cdp_cmd(
                "Network.getResponseBody",
                {"requestId": request_id},
            )
            raw_body = body.get("body", "")
            if body.get("base64Encoded"):
                raw_content = base64.b64decode(raw_body)
            else:
                raw_content = raw_body.encode("utf-8")

            if not raw_content:
                continue

            seen_request_ids.add(request_id)
            responses.append(
                (response_url, raw_content, content_type)
            )
        except Exception:
            # Be tolerant of browser-internal responses that disappear
            # before Network.getResponseBody is called.
            continue

    return responses


def render_page_capture_network(
    url,
    wait_seconds=5,
    select_year=None,
):
    """
    Render halaman dan tangkap respons fetch/XHR, termasuk Next.js Flight
    (text/x-component). select_year memicu permintaan data untuk tahun target.
    """
    def operation(driver):
        driver.execute_cdp_cmd("Network.enable", {})
        driver.set_page_load_timeout(75)

        initial_url = (
            _remove_year_param(url)
            if select_year is not None
            else url
        )
        _safe_get(driver, initial_url)
        time.sleep(min(wait_seconds, 2))

        try:
            initial_entries = driver.get_log("performance")
        except Exception:
            initial_entries = []

        selection_method = "not_requested"
        if select_year is not None:
            selection_method = _select_year_in_browser(
                driver,
                url,
                select_year,
                wait_seconds=min(wait_seconds, 4),
            )

        time.sleep(wait_seconds)

        try:
            later_entries = driver.get_log("performance")
        except Exception:
            later_entries = []

        network_responses = _capture_network_responses(
            driver,
            list(initial_entries) + list(later_entries),
        )

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
            try:
                screenshot = driver.get_screenshot_as_png()
            except Exception:
                screenshot = b""

        rendered_html = driver.page_source or ""
        print(
            f"    [BROWSER] Network capture: "
            f"{len(network_responses)} respons; "
            f"year={select_year or '-'}; "
            f"selection={selection_method}"
        )
        return rendered_html, network_responses, screenshot

    return _run_with_chrome(operation, "Render dan capture network")


def extract_browser(
    url,
    instruction,
    tahun,
    decimal=None,
    thousands=None,
    select_year=None,
):
    try:
        html = render_page(url, select_year=select_year)
    except Exception as error:
        return ExtractionResult(
            status="NEED_PARSER",
            source_type="browser",
            message=(
                "Chrome/browser gagal merender halaman: "
                f"{error.__class__.__name__}: "
                f"{str(error).splitlines()[0][:180]}"
            ),
            evidence=["browser_render_failed=true"],
        )

    try:
        read_html_options = {}
        if decimal is not None:
            read_html_options["decimal"] = decimal
        if thousands is not None:
            read_html_options["thousands"] = thousands
        frames = pd.read_html(
            StringIO(html),
            **read_html_options,
        )
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
            "Browser berhasil merender halaman, tetapi tabel HTML "
            "tidak cocok dengan metrik/tahun/wilayah yang diminta."
        ),
        evidence=[f"html_tables={len(frames)}"],
    )
