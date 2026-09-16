import ast
import html as html_lib
import operator
import os
import re
import time
import zipfile
import json
from io import BytesIO, StringIO
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import pandas as pd
import pdfplumber
import requests

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait



SCRAPER_BUILD = "33.c-v20-perkapita-bps"

# ============================================================
# KONFIGURASI
# ============================================================

# URL/token tidak lagi di-hardcode di repo (repo publik).
# Diambil dari environment (.env) bila modul ini perlu push ke Apps Script.
APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "").strip()

APPS_SCRIPT_TOKEN = os.getenv("APPS_SCRIPT_TOKEN", "").strip()

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}

BI_JANUARY_PAGE_TEMPLATE = (
    "https://www.bi.go.id/id/statistik/ekonomi-keuangan/seki/Pages/"
    "SEKI-JANUARI-{year}.aspx"
)

SCRAPE_CACHE = {}

# ============================================================
# MODE INCREMENTAL
# ============================================================

# False = data Kode+Wilayah+Tahun yang sudah SUCCESS dilewati.
# True  = scrape ulang semua data.
FORCE_REFRESH = False

# Kalau hanya indikator tertentu yang ingin diulang, isi kodenya di sini.
# Contoh:
# FORCE_REFRESH_CODES = {"33.a", "33.b"}
FORCE_REFRESH_CODES = set()


# ============================================================
# NORMALISASI HEADER SHEET
# ============================================================

def normalize_header(text):
    text = str(text or "").strip().lower()

    text = re.sub(
        r"[^a-z0-9]+",
        "_",
        text
    )

    return text.strip("_")


HEADER_ALIASES = {
    "kode": "Kode",

    "nama_indikator": "Nama_Indikator",
    "nama": "Nama_Indikator",
    "indikator": "Nama_Indikator",

    "tahun": "Tahun",

    "wilayah": "Wilayah",
    "provinsi": "Wilayah",
    "kab_kota": "Wilayah",
    "kabupaten_kota": "Wilayah",

    "sumber_1": "Sumber_1",
    "sumber1": "Sumber_1",
    "url_1": "Sumber_1",
    "url1": "Sumber_1",

    "data_diambil_1": "Data_Diambil_1",
    "ambil_data_1": "Data_Diambil_1",
    "data_yang_diambil_1": "Data_Diambil_1",
    "target_1": "Data_Diambil_1",

    "sumber_2": "Sumber_2",
    "sumber2": "Sumber_2",
    "url_2": "Sumber_2",
    "url2": "Sumber_2",

    "data_diambil_2": "Data_Diambil_2",
    "ambil_data_2": "Data_Diambil_2",
    "data_yang_diambil_2": "Data_Diambil_2",
    "target_2": "Data_Diambil_2",

    "sumber_3": "Sumber_3",
    "sumber3": "Sumber_3",
    "sumber_data_3": "Sumber_3",
    "url_3": "Sumber_3",
    "url3": "Sumber_3",

    "data_diambil_3": "Data_Diambil_3",
    "ambil_data_3": "Data_Diambil_3",
    "data_yang_diambil_3": "Data_Diambil_3",
    "target_3": "Data_Diambil_3",

    "rumus": "Rumus",
    "formula": "Rumus",
}


# ============================================================
# APPS SCRIPT: HELPER GET + RETRY
# ============================================================

def _get_apps_script_json(action, label, attempts=3, timeout=60):
    """GET Apps Script dengan retry untuk timeout/404 sementara Google."""

    last_error = None

    for attempt in range(1, attempts + 1):
        try:
            print(f"[{label}] Percobaan koneksi {attempt}/{attempts}...")

            response = requests.get(
                APPS_SCRIPT_URL,
                params={
                    "token": APPS_SCRIPT_TOKEN,
                    "action": action,
                },
                timeout=timeout,
                allow_redirects=True,
            )

            response.raise_for_status()

            try:
                payload = response.json()
            except Exception as error:
                raise RuntimeError(
                    "Response Apps Script bukan JSON:\n"
                    + response.text[:500]
                ) from error

            if not payload.get("success"):
                raise RuntimeError(
                    f"Apps Script gagal untuk action '{action}': "
                    + str(payload.get("error", "Unknown error"))
                )

            return payload

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError,
            requests.exceptions.HTTPError,
            RuntimeError,
        ) as error:
            last_error = error

            print(f"[{label}] Koneksi gagal: {error}")

            if attempt < attempts:
                print(f"[{label}] Mencoba kembali...")
                time.sleep(min(2 * attempt, 5))

    raise RuntimeError(
        f"Gagal mengakses Apps Script setelah {attempts} percobaan. "
        f"Error terakhir: {last_error}"
    )


# ============================================================
# APPS SCRIPT: BACA MASTER
# ============================================================

def read_master_sheet():

    print(
        "\n[MASTER] Membaca Master_Indikator "
        "melalui Apps Script..."
    )

    payload = _get_apps_script_json(
        action="master",
        label="MASTER",
        attempts=3,
        timeout=60,
    )

    records = payload.get("data", [])

    if not records:
        print("[MASTER] Tidak ada indikator.")
        return pd.DataFrame()

    df = pd.DataFrame(records)

    rename_map = {}

    for column in df.columns:
        normalized = normalize_header(column)

        if normalized in HEADER_ALIASES:
            rename_map[column] = HEADER_ALIASES[normalized]

    df = df.rename(columns=rename_map)

    required_columns = [
        "Kode",
        "Nama_Indikator",
        "Tahun",
        "Sumber_1",
        "Data_Diambil_1",
        "Sumber_2",
        "Data_Diambil_2",
        "Sumber_3",
        "Data_Diambil_3",
        "Rumus",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:
        raise RuntimeError(
            "Kolom Master_Indikator tidak lengkap: "
            + ", ".join(missing)
        )

    df["Kode"] = (
        df["Kode"]
        .astype(str)
        .str.strip()
    )

    df = df[
        (df["Kode"] != "")
        &
        (df["Kode"].str.lower() != "nan")
    ].copy()

    print(f"[MASTER] {len(df)} indikator ditemukan.")

    return df



# ============================================================
# APPS SCRIPT: BACA HASIL YANG SUDAH ADA
# ============================================================

def normalize_wilayah_key(value):
    """Normalisasi Wilayah untuk key incremental."""
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "none", "-"}:
        return ""
    return re.sub(r"\s+", " ", text).lower()


def read_existing_results():
    """
    Return dict dengan key:
        (Kode, Wilayah_normalized, Tahun)
    """
    print(
        "\n[HASIL] Membaca Hasil_Indikator "
        "untuk pengecekan incremental..."
    )

    try:
        payload = _get_apps_script_json(
            action="results",
            label="HASIL",
            attempts=3,
            timeout=60,
        )

        records = payload.get("data", [])
        existing = {}

        for record in records:
            kode = str(record.get("Kode", "")).strip()
            status = str(record.get("Status", "")).strip().upper()
            wilayah_key = normalize_wilayah_key(
                record.get("Wilayah", "")
            )

            if not kode:
                continue

            try:
                tahun = int(float(record.get("Tahun", "")))
            except (TypeError, ValueError):
                continue

            existing[(kode, wilayah_key, tahun)] = {
                "status": status,
                "record": record,
            }

        success_total = sum(
            1
            for item in existing.values()
            if item.get("status") == "SUCCESS"
        )

        print(
            f"[HASIL] {len(existing)} "
            "baris Kode+Wilayah+Tahun ditemukan."
        )
        print(
            f"[HASIL] {success_total} "
            "baris berstatus SUCCESS."
        )

        return existing

    except Exception as error:
        print(
            "[HASIL] WARNING: Hasil_Indikator "
            "tidak dapat dibaca."
        )
        print(f"[HASIL] Detail: {error}")
        print(
            "[HASIL] Mode aman: data akan diproses "
            "seperti biasa karena status lama "
            "tidak dapat diverifikasi."
        )
        return {}



def normalize_force_refresh_codes():
    return {
        str(code).strip()
        for code in FORCE_REFRESH_CODES
        if str(code).strip()
    }


def should_skip_existing(
    kode,
    tahun,
    existing_results,
    wilayah=None,
):
    """Skip berdasarkan Kode + Wilayah + Tahun."""
    if FORCE_REFRESH:
        return False

    kode = str(kode or "").strip()
    tahun = int(tahun)

    if kode in normalize_force_refresh_codes():
        return False

    if wilayah is not None:
        key = (
            kode,
            normalize_wilayah_key(wilayah),
            tahun,
        )

        existing = existing_results.get(key)

        if not existing:
            return False

        if existing.get("status") != "SUCCESS":
            return False

        record = existing.get("record", {}) or {}
        old_region = str(
            record.get("Wilayah", "") or ""
        ).strip()

        if (
            not old_region
            or old_region.lower() in {"nan", "none", "-"}
        ):
            print(
                f"  [REFRESH] [{kode}] {tahun}: "
                "SUCCESS lama belum memiliki Wilayah."
            )
            return False

        return True

    # Kompatibilitas caller lama yang belum mengirim wilayah.
    for key, item in existing_results.items():
        if not isinstance(key, tuple):
            continue

        if len(key) == 3:
            key_kode, _, key_tahun = key
        elif len(key) == 2:
            key_kode, key_tahun = key
        else:
            continue

        if (
            str(key_kode).strip() == kode
            and int(key_tahun) == tahun
            and item.get("status") == "SUCCESS"
        ):
            return True

    return False


# ============================================================
# APPS SCRIPT: KIRIM HASIL
# ============================================================

def send_results_to_sheet(
    results
):
    """
    Mengirim hasil ke Apps Script.
    """
    
    # FILTER HANYA SUCCESS (FAILED tidak masuk sheet, hanya di log terminal)
    results = [r for r in results if r.get("status") == "SUCCESS"]

    if not results:
        print("\n[SHEET] Tidak ada hasil SUCCESS yang baru untuk dikirim.")
        return

    print("\n[SHEET] Mengirim hasil SUCCESS ke Hasil_Indikator...")

    try:

        # ====================================================
        # POST KE APPS SCRIPT
        #
        # Jangan ikuti redirect otomatis. Kalau Google memberi
        # 302/303 menuju script.googleusercontent.com, kita baca
        # response final secara manual memakai GET.
        # ====================================================

        response = requests.post(
            APPS_SCRIPT_URL,
            json={
                "token":
                    APPS_SCRIPT_TOKEN,

                "results":
                    results,
            },
            timeout=120,
            allow_redirects=False,
        )

        # ====================================================
        # REDIRECT NORMAL DARI APPS SCRIPT CONTENTSERVICE
        # ====================================================

        if response.status_code in {
            301,
            302,
            303,
            307,
            308,
        }:

            redirect_url = (
                response.headers.get(
                    "Location"
                )
            )

            print(
                "[SHEET] Request diterima Apps Script."
            )

            if not redirect_url:

                print(
                    "[SHEET] Redirect Google tidak memiliki "
                    "Location header."
                )

                print(
                    "[SHEET] Cek Hasil_Indikator untuk "
                    "memastikan data sudah masuk."
                )

                return

            # =================================================
            # BACA RESPONSE FINAL TANPA MENGIRIM POST ULANG
            # =================================================

            try:

                final_response = requests.get(
                    redirect_url,
                    headers=REQUEST_HEADERS,
                    timeout=30,
                    allow_redirects=True,
                )

                final_response.raise_for_status()

            except (
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError,
            ) as error:

                print(
                    "[SHEET] Data sudah dikirim ke Apps Script."
                )

                print(
                    "[SHEET] Response akhir Google tidak dapat "
                    "dibaca karena timeout/koneksi."
                )

                print(
                    "[SHEET] Tidak melakukan POST ulang untuk "
                    "menghindari pengiriman ganda."
                )

                print(
                    "[SHEET] Cek Hasil_Indikator. "
                    "Jika baris sudah masuk, proses selesai."
                )

                return

            # =================================================
            # PARSE JSON RESPONSE FINAL
            # =================================================

            try:

                data = (
                    final_response.json()
                )

            except Exception:

                print(
                    "[SHEET] Apps Script menerima request, "
                    "tetapi response final bukan JSON."
                )

                print(
                    "[SHEET] Cek Hasil_Indikator untuk "
                    "memastikan data sudah masuk."
                )

                return

            if not data.get(
                "success"
            ):

                raise RuntimeError(
                    "Apps Script error: "
                    + str(
                        data.get(
                            "error",
                            "Unknown error"
                        )
                    )
                )

            print(
                f"[SHEET] Total diproses  : "
                f"{data.get('total_processed', 0)}"
            )

            print(
                f"[SHEET] Baris baru      : "
                f"{data.get('total_inserted', 0)}"
            )

            print(
                f"[SHEET] Baris diperbarui: "
                f"{data.get('total_updated', 0)}"
            )

            return

        # ====================================================
        # RESPONSE LANGSUNG TANPA REDIRECT
        # ====================================================

        response.raise_for_status()

        try:

            data = (
                response.json()
            )

        except Exception:

            print(
                "[SHEET] Request berhasil dikirim, "
                "tetapi response bukan JSON."
            )

            print(
                "[SHEET] Cek Hasil_Indikator untuk "
                "memastikan data sudah masuk."
            )

            return

        if not data.get(
            "success"
        ):

            raise RuntimeError(
                "Apps Script error: "
                + str(
                    data.get(
                        "error",
                        "Unknown error"
                    )
                )
            )

        print(
            f"[SHEET] Total diproses  : "
            f"{data.get('total_processed', 0)}"
        )

        print(
            f"[SHEET] Baris baru      : "
            f"{data.get('total_inserted', 0)}"
        )

        print(
            f"[SHEET] Baris diperbarui: "
            f"{data.get('total_updated', 0)}"
        )

    except requests.exceptions.Timeout:

        print(
            "[SHEET] WARNING: koneksi ke Apps Script timeout."
        )

        print(
            "[SHEET] Cek Hasil_Indikator sebelum "
            "menjalankan ulang scraper."
        )

        return

    except requests.exceptions.ConnectionError as error:

        print(
            "[SHEET] WARNING: koneksi ke Google bermasalah."
        )

        print(
            f"[SHEET] Detail: {error}"
        )

        print(
            "[SHEET] Cek Hasil_Indikator sebelum "
            "menjalankan ulang scraper."
        )

        return


# ============================================================
# HELPER SHEET
# ============================================================

def cell_value(
    row,
    column
):

    value = row.get(
        column
    )

    if pd.isna(
        value
    ):

        return ""

    return str(
        value
    ).strip()


# ============================================================
# PARSE TAHUN
# ============================================================

def parse_years(
    value
):

    text = str(
        value or ""
    ).strip()

    if not text:

        return []

    text = (
        text
        .replace(
            "–",
            "-"
        )
        .replace(
            "—",
            "-"
        )
    )

    range_match = re.fullmatch(
        r"\s*(\d{4})\s*-\s*(\d{4})\s*",
        text
    )

    if range_match:

        start_year = int(
            range_match.group(1)
        )

        end_year = int(
            range_match.group(2)
        )

        if start_year > end_year:

            raise ValueError(
                f"Rentang tahun "
                f"tidak valid: {text}"
            )

        return list(
            range(
                start_year,
                end_year + 1
            )
        )

    if re.fullmatch(
        r"\d{4}"
        r"(?:\s*[,;]\s*\d{4})+",
        text
    ):

        years = [
            int(
                item.strip()
            )
            for item
            in re.split(
                r"[,;]",
                text
            )
        ]

        return list(
            dict.fromkeys(
                years
            )
        )

    if re.fullmatch(
        r"\d{4}(?:\.0+)?",
        text
    ):

        return [
            int(
                float(
                    text
                )
            )
        ]

    raise ValueError(
        "Format Tahun tidak dikenali. "
        "Gunakan 2025 atau 2019-2025."
    )


# ============================================================
# FORMAT ANGKA
# ============================================================

def format_id(
    value,
    decimals=2
):

    if value is None:

        return "-"

    text = (
        f"{float(value):,.{decimals}f}"
    )

    return (
        text
        .replace(
            ",",
            "X"
        )
        .replace(
            ".",
            ","
        )
        .replace(
            "X",
            "."
        )
    )


# ============================================================
# PARSE ANGKA BI
# ============================================================

def parse_bi_number(
    value
):

    if value is None:

        return None

    try:

        if pd.isna(
            value
        ):

            return None

    except Exception:

        pass

    if isinstance(
        value,
        (int, float)
    ):

        return float(
            value
        )

    text = (
        str(
            value
        )
        .strip()
    )

    text = re.sub(
        r"[^0-9,.\-]",
        "",
        text
    )

    if not text:

        return None

    try:

        if "," in text:

            text = text.replace(
                ",",
                ""
            )

        return float(
            text
        )

    except ValueError:

        return None


# ============================================================
# PARSE ANGKA INDONESIA
# ============================================================

def parse_indonesian_number(
    value
):

    if value is None:

        return None

    try:

        if pd.isna(
            value
        ):

            return None

    except Exception:

        pass

    if isinstance(
        value,
        (int, float)
    ):

        return float(
            value
        )

    text = re.sub(
        r"[^0-9.,\-]",
        "",
        str(
            value
        ).strip()
    )

    if not text:

        return None

    try:

        if "," in text:

            text = (
                text
                .replace(
                    ".",
                    ""
                )
                .replace(
                    ",",
                    "."
                )
            )

        elif re.fullmatch(
            r"-?\d{1,3}"
            r"(?:\.\d{3})+",
            text
        ):

            text = text.replace(
                ".",
                ""
            )

        return float(
            text
        )

    except ValueError:

        return None


# ============================================================
# SAFE FORMULA
# ============================================================

ALLOWED_BINARY_OPERATORS = {
    ast.Add:
        operator.add,

    ast.Sub:
        operator.sub,

    ast.Mult:
        operator.mul,

    ast.Div:
        operator.truediv,

    ast.Pow:
        operator.pow,
}


ALLOWED_UNARY_OPERATORS = {
    ast.UAdd:
        operator.pos,

    ast.USub:
        operator.neg,
}


def calculate_formula(
    expression,
    variables
):

    expression = (
        str(
            expression or ""
        )
        .strip()
        or
        "S1"
    )

    tree = ast.parse(
        expression,
        mode="eval"
    )

    def evaluate(
        node
    ):

        if isinstance(
            node,
            ast.Expression
        ):

            return evaluate(
                node.body
            )

        if (
            isinstance(
                node,
                ast.Constant
            )
            and
            isinstance(
                node.value,
                (int, float)
            )
        ):

            return node.value

        if isinstance(
            node,
            ast.Name
        ):

            if node.id not in variables:

                raise ValueError(
                    f"Variabel "
                    f"{node.id} "
                    "tidak ditemukan"
                )

            value = (
                variables[
                    node.id
                ]
            )

            if value is None:

                raise ValueError(
                    f"Variabel "
                    f"{node.id} kosong"
                )

            return float(
                value
            )

        if isinstance(
            node,
            ast.BinOp
        ):

            operator_type = (
                type(
                    node.op
                )
            )

            if (
                operator_type
                not in
                ALLOWED_BINARY_OPERATORS
            ):

                raise ValueError(
                    "Operator formula "
                    "tidak diizinkan"
                )

            return (
                ALLOWED_BINARY_OPERATORS[
                    operator_type
                ](
                    evaluate(
                        node.left
                    ),
                    evaluate(
                        node.right
                    ),
                )
            )

        if isinstance(
            node,
            ast.UnaryOp
        ):

            operator_type = (
                type(
                    node.op
                )
            )

            if (
                operator_type
                not in
                ALLOWED_UNARY_OPERATORS
            ):

                raise ValueError(
                    "Operator unary "
                    "tidak diizinkan"
                )

            return (
                ALLOWED_UNARY_OPERATORS[
                    operator_type
                ](
                    evaluate(
                        node.operand
                    )
                )
            )

        raise ValueError(
            "Formula mengandung ekspresi "
            "yang tidak diizinkan"
        )

    return float(
        evaluate(
            tree
        )
    )


# ============================================================
# PDF
# ============================================================

def extract_pdf_pages(
    pdf_bytes
):

    pages = []

    with pdfplumber.open(
        BytesIO(
            pdf_bytes
        )
    ) as pdf:

        for page in pdf.pages:

            text = (
                page.extract_text(
                    x_tolerance=2,
                    y_tolerance=3,
                )
                or ""
            )

            pages.append(
                text
            )

    return pages


# ============================================================
# BI: CARI ZIP
# ============================================================

def find_zip_url_in_html(
    page_url,
    html_text
):

    matches = re.findall(
        r'href\s*=\s*["\']'
        r'([^"\']+\.zip'
        r'(?:\?[^"\']*)?)'
        r'["\']',
        html_text,
        flags=re.IGNORECASE,
    )

    for href in matches:

        href = (
            html_lib.unescape(
                href.strip()
            )
        )

        if "seki" in href.lower():

            return urljoin(
                page_url,
                href
            )

    return None


# ============================================================
# SELENIUM / CHROME
#
# CATATAN: fungsi-fungsi di bawah ini SEBELUMNYA didefinisikan di
# dalam bps.py sehingga hanya bisa dipakai oleh bps.py sendiri.
# Modul lain (mis. ojk.py) yang cuma "from utils import *" tidak
# pernah bisa mengaksesnya -> NameError: name 'create_chrome_driver'
# is not defined.
#
# Dipindah ke sini supaya SEMUA modul yang sudah "from utils import *"
# (ojk.py, bps.py, bi_i19.py, dst) otomatis bisa memakainya tanpa
# perlu ubah import satu-satu.
# ============================================================

def set_year_parameter(
    url,
    tahun
):

    parsed = urlparse(
        url
    )

    query = parse_qs(
        parsed.query
    )

    query[
        "year"
    ] = [
        str(
            int(
                tahun
            )
        )
    ]

    return urlunparse(
        parsed._replace(
            query=urlencode(
                query,
                doseq=True,
            )
        )
    )


def create_chrome_driver():

    options = webdriver.ChromeOptions()

    options.add_argument("--headless=new")

    options.add_argument(
        "--disable-gpu"
    )

    options.add_argument(
        "--no-sandbox"
    )

    options.add_argument(
        "--disable-dev-shm-usage"
    )

    options.add_argument(
        "--window-size=1920,1080"
    )

    options.add_argument(
        "--log-level=3"
    )

    # PENTING:
    # aktifkan Chrome Performance Log
    options.set_capability(
        "goog:loggingPrefs",
        {
            "performance": "ALL"
        }
    )

    return webdriver.Chrome(
        options=options
    )


def collect_page_text(
    driver
):

    texts = []

    try:

        text = (
            driver
            .execute_script(
                "return document.body "
                "? document.body.innerText "
                ": '';"
            )
        )

        if text:

            texts.append(
                text
            )

    except Exception:

        pass

    try:

        driver.switch_to.default_content()

        frame_count = len(
            driver.find_elements(
                By.TAG_NAME,
                "iframe",
            )
        )

        for index in range(
            frame_count
        ):

            driver.switch_to.default_content()

            frames = (
                driver.find_elements(
                    By.TAG_NAME,
                    "iframe",
                )
            )

            if index >= len(
                frames
            ):

                continue

            try:

                driver.switch_to.frame(
                    frames[
                        index
                    ]
                )

                time.sleep(
                    1
                )

                text = (
                    driver
                    .execute_script(
                        "return document.body "
                        "? document.body.innerText "
                        ": '';"
                    )
                )

                if text:

                    texts.append(
                        text
                    )

            except Exception:

                continue

    finally:

        driver.switch_to.default_content()

    return "\n".join(
        texts
    )


def wait_past_security_page(driver, timeout=25):
    start = time.time()
    blocked_markers = [
        "performing security verification",
        "just a moment",
        "checking your browser",
        "akses ini ditolak",
        "access is blocked",
    ]
    while time.time() - start < timeout:
        text = (driver.page_source or "").lower()
        if not any(marker in text for marker in blocked_markers):
            return True
        time.sleep(2)
    return False