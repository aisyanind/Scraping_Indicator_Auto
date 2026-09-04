"""
Pusat validasi & fallback URL sumber untuk semua adapter.

Prinsip:
1. URL http(s) yang valid WAJIB dihormati — sumber user dipakai apa adanya.
2. URL tidak valid / kosong / nama indikator:
   - jika ada `canonical` (sumber standar stabil per indikator) -> fallback,
     dengan menandai `used_default=True` supaya laporan transparan.
   - jika tidak ada `canonical` -> raise LinkTidakLengkapError sehingga
     router mengubahnya menjadi status LINK_TIDAK_LENGKAP (bukan NEED_PARSER).
"""

import re
import threading

# Thread-local: URL sumber standar yang dipakai karena Sumber_1 tidak valid.
# Dibaca oleh legacy_bridge saat membungkus ExtractionResult.
_local = threading.local()


class LinkTidakLengkapError(RuntimeError):
    """Sumber_1 bukan URL yang valid dan tidak ada sumber kanonik."""


def is_http_url(value):
    return bool(re.match(r"^https?://", str(value or "").strip()))


def mark_default_used(url):
    _local.used_default = str(url or "")


def pop_default_used():
    url = getattr(_local, "used_default", None)
    _local.used_default = None
    return url


def resolve_source_url(raw_url, canonical=None):
    """
    Tentukan URL sumber yang dipakai.

    Return (url_efektif, used_default: bool).

    - raw_url http valid            -> (raw_url, False)
    - raw_url bukan URL + canonical -> (canonical, True)
    - raw_url bukan URL, no canonical -> raise LinkTidakLengkapError
    """
    source_url = str(raw_url or "").strip()

    if is_http_url(source_url):
        return source_url, False

    canonical_url = str(canonical or "").strip()

    if is_http_url(canonical_url):
        print(
            "    [SOURCE RESOLVER] Sumber_1 bukan URL yang valid. "
            f"Memakai sumber standar: {canonical_url}"
        )
        mark_default_used(canonical_url)
        return canonical_url, True

    raise LinkTidakLengkapError(
        "SOURCE_LINK_TIDAK_LENGKAP: Sumber_1 bukan URL yang valid. "
        "Mohon isi link lengkap (https://...) pada kolom Sumber_1."
    )
