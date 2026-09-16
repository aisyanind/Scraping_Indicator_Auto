import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

APPS_SCRIPT_URL = os.getenv("APPS_SCRIPT_URL", "").strip()
APPS_SCRIPT_TOKEN = os.getenv("APPS_SCRIPT_TOKEN", "").strip()

# Modul legacy kini dibundel di dalam repo (legacy_indicators/).
# LEGACY_INDICATORS_PATH dari .env hanya dipakai bila foldernya benar-benar
# ada; jika tidak, otomatis jatuh ke folder bundel. Dengan begitu hasil
# clone di mesin mana pun langsung lengkap tanpa folder legacy eksternal.
_BUNDLED_LEGACY_PATH = Path(__file__).resolve().parent / "legacy_indicators"
_ENV_LEGACY_PATH = os.getenv("LEGACY_INDICATORS_PATH", "").strip()

if _ENV_LEGACY_PATH and Path(_ENV_LEGACY_PATH).is_dir():
    LEGACY_INDICATORS_PATH = _ENV_LEGACY_PATH
else:
    LEGACY_INDICATORS_PATH = str(_BUNDLED_LEGACY_PATH)

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
