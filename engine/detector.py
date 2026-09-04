import json
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlparse

import pandas as pd
import requests

from config import REQUEST_HEADERS


@dataclass
class SourcePayload:
    url: str
    kind: str
    content: bytes
    content_type: str
    final_url: str


def _kind_from_url(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".pdf"):
        return "pdf"
    if path.endswith((".xlsx", ".xls")):
        return "excel"
    if path.endswith(".csv"):
        return "csv"
    if path.endswith(".json"):
        return "json"
    if path.endswith(".zip"):
        return "zip"
    return ""


def _is_excel_zip(content) -> bool:
    try:
        pd.ExcelFile(BytesIO(content))
        return True
    except Exception:
        return False


def _zip_inner_kinds(content) -> set[str]:
    """
    Buka ZIP dan tebak isi member-nya (dari ekstensi + magic bytes).
    Return set kind: {"csv"}, {"xlsx"}, {"pdf"}, dsb.
    """
    import zipfile

    kinds = set()

    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            names = archive.namelist()
    except Exception:
        return kinds

    for name in names:
        lowered = name.lower()
        if lowered.endswith(".csv"):
            kinds.add("csv")
        elif lowered.endswith((".xlsx", ".xls")):
            kinds.add("excel")
        elif lowered.endswith(".pdf"):
            kinds.add("pdf")
        elif lowered.endswith(".zip"):
            kinds.add("zip")

    return kinds


def _looks_like_spa(text: str) -> bool:
    markers = (
        "__NEXT_DATA__",
        "_next/static",
        "BAILOUT_TO_CLIENT_SIDE_RENDERING",
        "webpackChunk",
        "window.__INITIAL_STATE__",
        'id="__next"',
        "data-reactroot",
    )
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in markers)


def detect_kind(
    content: bytes,
    content_type: str = "",
    url: str = "",
) -> str:
    """
    Deteksi tipe sumber dari magic bytes, content-type, lalu URL.

    Khusus:
    - `PK` bisa xlsx ATAU zip biasa. Jika bukan xlsx -> kind="zip".
    - HTML dengan marker SPA/portal -> kind="spa".
    """
    kind = _kind_from_url(url)

    if content[:4] == b"%PDF":
        return "pdf"

    if content[:2] == b"PK":
        if _is_excel_zip(content):
            return "excel"
        if kind == "zip":
            return "zip"
        return "zip"

    if kind:
        return kind

    content_type = (content_type or "").split(";")[0].strip().lower()

    if "spreadsheet" in content_type or "excel" in content_type:
        return "excel"
    if "csv" in content_type:
        return "csv"
    if "json" in content_type:
        return "json"
    if "zip" in content_type:
        return "zip"
    if "html" in content_type:
        return "spa" if _looks_like_spa(
            content[:200_000].decode("utf-8", "replace")
        ) else "html"

    # Fallback: coba JSON lalu HTML.
    try:
        json.loads(content.decode("utf-8"))
        return "json"
    except Exception:
        text = content[:200_000].decode("utf-8", "replace")
        return "spa" if _looks_like_spa(text) else "html"


def fetch_and_detect(url: str, timeout: int = 90) -> SourcePayload:
    url = str(url or "").strip()
    if not url:
        raise ValueError("URL sumber kosong")

    response = requests.get(
        url,
        headers=REQUEST_HEADERS,
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()

    content = response.content
    content_type = (
        response.headers.get("Content-Type", "")
        .split(";")[0]
        .strip()
        .lower()
    )

    kind = detect_kind(
        content,
        content_type=content_type,
        url=response.url,
    )

    return SourcePayload(
        url=url,
        kind=kind,
        content=content,
        content_type=content_type,
        final_url=response.url,
    )
