import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "").strip()
APPS_SCRIPT_TOKEN = os.getenv("APPS_SCRIPT_TOKEN", "").strip()
LEGACY_INDICATORS_PATH = os.getenv(
    "LEGACY_INDICATORS_PATH",
    r"D:\MagangHub\Scraping_Indikator\indicators",
).strip()

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "10"))
JOB_TIMEOUT_SECONDS = int(os.getenv("JOB_TIMEOUT_SECONDS", "900"))
AUTO_CONFIDENCE_THRESHOLD = float(
    os.getenv("AUTO_CONFIDENCE_THRESHOLD", "0.80")
)
REVIEW_CONFIDENCE_THRESHOLD = float(
    os.getenv("REVIEW_CONFIDENCE_THRESHOLD", "0.55")
)

# Konkurensi worker
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "5"))
PER_DOMAIN_LIMIT = int(os.getenv("PER_DOMAIN_LIMIT", "1"))
APPS_SCRIPT_CONCURRENCY = int(os.getenv("APPS_SCRIPT_CONCURRENCY", "2"))
INNER_CONCURRENCY = int(os.getenv("INNER_CONCURRENCY", "3"))

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}

DEFAULT_WILAYAH = "Prov. Kalimantan Selatan"

KALSEL_WILAYAHS = [
    "Prov. Kalimantan Selatan",
    "Kab. Tanah Laut",
    "Kab. Kotabaru",
    "Kab. Banjar",
    "Kab. Barito Kuala",
    "Kab. Tapin",
    "Kab. Hulu Sungai Selatan",
    "Kab. Hulu Sungai Tengah",
    "Kab. Hulu Sungai Utara",
    "Kab. Tabalong",
    "Kab. Tanah Bumbu",
    "Kab. Balangan",
    "Kota Banjarmasin",
    "Kota Banjarbaru",
]
