"""
Regresi indikator IKLH/IKLHD (Open Data Kalsel):

1. Routing — URL opendata.kalselprov walaupun nama indikatornya
   "...Lingkungan Hidup Daerah" harus jatuh ke adapter iklh,
   bukan iklhd (slugsatudata tidak ada di portal satudata).
2. Parser — header CSV portal bisa berubah kapitalisasi
   (Kd_wil -> kd_wil) dan wajib tetap terbaca.
3. Download — portal sering 500 sesaat; unduh harus retry.
"""

from types import SimpleNamespace

import pytest
import requests

from sources import iklh_source
from sources.iklh_source import (
    _download_iklh_csv,
    _get_table,
    _parse_iklh_table,
    _retry_delay_seconds,
)
from sources.registry import AdapterContext, run_adapters

OPENDATA_URL = (
    "https://opendata.kalselprov.go.id/open-data/dataset/"
    "b43f5eef-565f-43f8-9f80-0aa9e0c079eb"
)
SATUDATA_URL = (
    "https://satudata.kemenlh.go.id/dataset/"
    "indeks-kualitas-lingkungan-hidup"
)
NAMA_IKLHD = "Indeks Kualitas Lingkungan Hidup Daerah"

CSV_KEDATAN = (
    "no.;kd_wil;kabupaten/kota;satuan;2016;2024\n"
    "1;6301;Kab. Tanah Laut;Indeks;56.71;68,57\n"
    "2;6371;Kota Banjarmasin;Indeks;60.12;70.5\n"
)


def _ctx(url, nama=NAMA_IKLHD, description=""):
    return AdapterContext(
        url=url,
        tahun=2024,
        description=description,
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator=nama,
    )


@pytest.fixture(autouse=True)
def _clear_iklh_state():
    iklh_source._TABLE_CACHE.clear()
    iklh_source._DOWNLOAD_FAILURE.clear()
    yield
    iklh_source._TABLE_CACHE.clear()
    iklh_source._DOWNLOAD_FAILURE.clear()


def _sentinel(value):
    def handler(**_kwargs):
        return value

    return handler


# ---------------------------------------------------------------------------
# 1. Routing registry
# ---------------------------------------------------------------------------

def test_opendata_url_routes_to_iklh(monkeypatch):
    monkeypatch.setattr(
        "sources.iklh_source.scrape_iklh",
        _sentinel({"iklh": 1}),
    )
    monkeypatch.setattr(
        "sources.satudata_source.scrape_iklhd",
        _sentinel({"iklhd": 1}),
    )

    result = run_adapters(_ctx(OPENDATA_URL))

    assert result is not None
    assert result.status == "SUCCESS"
    assert result.source_type == "legacy:iklh"
    assert result.value == {"iklh": 1}


def test_opendata_url_with_iklhd_desc_routes_to_iklh(monkeypatch):
    monkeypatch.setattr(
        "sources.iklh_source.scrape_iklh",
        _sentinel({"iklh": 1}),
    )
    monkeypatch.setattr(
        "sources.satudata_source.scrape_iklhd",
        _sentinel({"iklhd": 1}),
    )

    result = run_adapters(
        _ctx(
            OPENDATA_URL,
            description=(
                "Kode IKLHD | Indeks Kualitas Lingkungan Hidup Daerah | "
                "nilai indikator per kab/kota dan prov"
            ),
        )
    )

    assert result is not None
    assert result.source_type == "legacy:iklh"


def test_satudata_url_still_routes_to_iklhd(monkeypatch):
    monkeypatch.setattr(
        "sources.iklh_source.scrape_iklh",
        _sentinel({"iklh": 1}),
    )
    monkeypatch.setattr(
        "sources.satudata_source.scrape_iklhd",
        _sentinel({"iklhd": 1}),
    )

    result = run_adapters(_ctx(SATUDATA_URL))

    assert result is not None
    assert result.status == "SUCCESS"
    assert result.source_type == "legacy:iklhd"
    assert result.value == {"iklhd": 1}


def test_unknown_url_keeps_old_iklhd_name_matching(monkeypatch):
    """Nama "...Daerah" tanpa URL yang dikenal tetap ke iklhd (lama)."""
    monkeypatch.setattr(
        "sources.iklh_source.scrape_iklh",
        _sentinel({"iklh": 1}),
    )
    monkeypatch.setattr(
        "sources.satudata_source.scrape_iklhd",
        _sentinel({"iklhd": 1}),
    )

    result = run_adapters(_ctx("https://example.test/data"))

    assert result is not None
    assert result.source_type == "legacy:iklhd"


# ---------------------------------------------------------------------------
# 2. Parser header CSV
# ---------------------------------------------------------------------------

def test_parse_iklh_table_lowercase_header():
    table = _parse_iklh_table(CSV_KEDATAN)

    assert len(table) == 2
    assert table["6301"]["nama"] == "Kab. Tanah Laut"
    assert table["6301"]["data"][2024] == 68.57
    assert table["6371"]["data"][2016] == 60.12


def test_parse_iklh_table_legacy_uppercase_header():
    csv_text = CSV_KEDATAN.replace(
        "kd_wil;kabupaten/kota",
        "Kd_wil;Kabupaten/kota",
    )

    table = _parse_iklh_table(csv_text)

    assert table["6301"]["data"][2024] == 68.57


def test_parse_iklh_table_missing_required_column():
    csv_text = (
        "no.;nama_lain;satuan;2024\n"
        "1;Kab. Tanah Laut;Indeks;68.57\n"
    )

    with pytest.raises(RuntimeError, match="Kolom wajib IKLH"):
        _parse_iklh_table(csv_text)


# ---------------------------------------------------------------------------
# 3. Download retry
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code=200, text="", content_type="text/csv"):
        self.status_code = status_code
        self.text = text
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} Server Error",
                response=self,
            )


def _install_requests(monkeypatch, responses):
    """Patch requests.get milik iklh_source; return list penampung call."""
    calls = {"count": 0}
    queue = list(responses)

    def fake_get(*_args, **_kwargs):
        calls["count"] += 1
        item = queue.pop(0) if queue else responses[-1]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(
        iklh_source,
        "requests",
        SimpleNamespace(
            get=fake_get,
            RequestException=requests.RequestException,
        ),
    )
    monkeypatch.setattr(
        iklh_source,
        "time",
        SimpleNamespace(
            sleep=lambda _seconds: None,
            monotonic=lambda: 0.0,
        ),
    )
    return calls


def test_download_retries_transient_500_then_succeeds(monkeypatch):
    calls = _install_requests(
        monkeypatch,
        [
            _FakeResponse(status_code=500, text="oops"),
            _FakeResponse(status_code=500, text="oops"),
            _FakeResponse(status_code=500, text="oops"),
            _FakeResponse(status_code=200, text=CSV_KEDATAN),
        ],
    )

    text = _download_iklh_csv(OPENDATA_URL)

    assert text == CSV_KEDATAN
    assert calls["count"] == 4


def test_download_retries_html_body_then_succeeds(monkeypatch):
    calls = _install_requests(
        monkeypatch,
        [
            _FakeResponse(
                status_code=200,
                text="<html><body>Just a moment...</body></html>",
                content_type="text/html",
            ),
            _FakeResponse(status_code=200, text=CSV_KEDATAN),
        ],
    )

    text = _download_iklh_csv(OPENDATA_URL)

    assert text == CSV_KEDATAN
    assert calls["count"] == 2


def test_download_raises_after_all_attempts_fail(monkeypatch):
    calls = _install_requests(
        monkeypatch,
        [_FakeResponse(status_code=500, text="failed")],
    )

    with pytest.raises(RuntimeError, match="setelah 6 percobaan"):
        _download_iklh_csv(OPENDATA_URL)

    assert calls["count"] == 6


def test_download_raises_html_error_when_all_attempts_are_html(
    monkeypatch,
):
    _install_requests(
        monkeypatch,
        [
            _FakeResponse(
                status_code=200,
                text="<html><body>challenge</body></html>",
                content_type="text/html",
            ),
        ],
    )

    with pytest.raises(RuntimeError, match="mengembalikan HTML"):
        _download_iklh_csv(OPENDATA_URL)


def test_retry_backoff_is_exponential_with_cap():
    assert [
        _retry_delay_seconds(attempt)
        for attempt in range(1, 6)
    ] == [3, 6, 12, 24, 30]


# ---------------------------------------------------------------------------
# 4. Single-flight download antar thread tahun
# ---------------------------------------------------------------------------

def test_get_table_downloads_once_for_concurrent_threads(monkeypatch):
    import threading

    calls = {"count": 0}
    started = threading.Event()
    release = threading.Event()

    def slow_download(_url):
        calls["count"] += 1
        started.set()
        release.wait(timeout=5)
        return CSV_KEDATAN

    monkeypatch.setattr(
        iklh_source,
        "_download_iklh_csv",
        slow_download,
    )

    results = {}
    errors = []

    def worker(name):
        try:
            results[name] = _get_table(OPENDATA_URL)
        except Exception as error:  # pragma: no cover
            errors.append(error)

    threads = [
        threading.Thread(target=worker, args=(year,))
        for year in (2019, 2023, 2024)
    ]
    for thread in threads:
        thread.start()

    assert started.wait(timeout=5)
    release.set()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert calls["count"] == 1
    assert set(results) == {2019, 2023, 2024}
    for table in results.values():
        assert len(table) == 2


def test_get_table_failure_fails_fast_for_siblings(monkeypatch):
    import threading

    calls = {"count": 0}

    def failing_download(_url):
        calls["count"] += 1
        raise RuntimeError("Portal Open Data Kalsel gagal mengunduh CSV")

    monkeypatch.setattr(
        iklh_source,
        "_download_iklh_csv",
        failing_download,
    )

    errors = []

    def worker():
        try:
            _get_table(OPENDATA_URL)
        except RuntimeError as error:
            errors.append(str(error))

    first = threading.Thread(target=worker)
    first.start()
    first.join(timeout=10)

    # Pemanggil berikutnya dalam TTL harus langsung gagal memakai
    # error yang di-cache, tanpa siklus retry panjang lagi.
    second = threading.Thread(target=worker)
    second.start()
    second.join(timeout=10)

    assert len(errors) == 2
    assert calls["count"] == 1
    assert all("gagal mengunduh CSV" in error for error in errors)
