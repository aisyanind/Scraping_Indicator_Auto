import pytest

from models import ExtractionResult
from sources import bps_pdf_adapter
from sources import bps_utils


@pytest.fixture(autouse=True)
def clear_pdf_download_cache():
    bps_pdf_adapter._PDF_DOWNLOAD_CACHE.clear()
    bps_pdf_adapter._PDF_REFERER_CACHE.clear()
    yield
    bps_pdf_adapter._PDF_DOWNLOAD_CACHE.clear()
    bps_pdf_adapter._PDF_REFERER_CACHE.clear()


def test_router_returns_completed_bps_pdf_failure_without_generic_retries(
    monkeypatch,
):
    from engine import router
    from engine.detector import SourcePayload

    calls = {}
    pdf_result = ExtractionResult(
        status="NEED_PARSER",
        source_type="bps_pdf",
        message="PDF terbaca tetapi nilai indikator tidak ditemukan.",
        evidence=["pdf_candidates=1"],
    )
    monkeypatch.setattr(router, "try_legacy_source", lambda **_kwargs: None)
    monkeypatch.setattr(
        router,
        "fetch_and_detect",
        lambda _url: SourcePayload(
            url="https://kalsel.bps.go.id/id/subject/economy.html",
            final_url="https://kalsel.bps.go.id/id/subject/economy.html",
            kind="html",
            content=b"<html>Economy</html>",
            content_type="text/html",
        ),
    )
    monkeypatch.setattr(router, "_try_bps_embedded_json", lambda *_args: None)
    monkeypatch.setattr(router, "_try_bps_next_data", lambda *_args: None)

    def fake_try_bps_pdf(**kwargs):
        calls.update(kwargs)
        return pdf_result

    monkeypatch.setattr(bps_pdf_adapter, "try_bps_pdf", fake_try_bps_pdf)
    monkeypatch.setitem(
        router.EXTRACTORS,
        "html",
        lambda *_args: ExtractionResult(
            status="NEED_PARSER",
            source_type="html",
            message="HTML tidak punya tabel langsung.",
        ),
    )
    monkeypatch.setattr(
        router,
        "extract_discovery",
        lambda *_args: ExtractionResult(
            status="NEED_PARSER",
            source_type="discovery",
            message="Tidak ada tabel/link data yang cocok.",
        ),
    )
    monkeypatch.setattr(
        "sources.generic_auto_extractor.try_generic_auto",
        lambda **_kwargs: None,
    )

    result = router.scrape_source_auto(
        url="https://kalsel.bps.go.id/id/subject/economy.html",
        tahun=2024,
        description="Pertumbuhan ekonomi",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Pertumbuhan Ekonomi",
    )

    assert calls["page_kind"] == "html"
    assert result.message == pdf_result.message
    assert result.evidence == ["pdf_candidates=1"]


def test_router_does_not_let_low_confidence_generic_result_skip_brin_adapter(
    monkeypatch,
):
    from engine import router
    from engine.detector import SourcePayload

    url = "https://example.test/idsd"
    generic_result = ExtractionResult(
        status="NEED_PARSER",
        value={"__main__": 67.0},
        confidence=0.46,
        source_type="generic_auto",
        message="Data dari auto-extract (perlu review): 67.0",
        evidence=["candidates=1"],
    )
    brin_result = ExtractionResult(
        status="DATA_TIDAK_ADA",
        source_type="legacy:brin",
        message="Seri IDSD dimulai tahun 2022.",
    )
    calls = {}

    monkeypatch.setattr(
        router,
        "fetch_and_detect",
        lambda _url: SourcePayload(
            url=url,
            final_url=url,
            kind="spa",
            content=b"<html>BRIN</html>",
            content_type="text/html",
        ),
    )
    monkeypatch.setitem(
        router.EXTRACTORS,
        "spa",
        lambda *_args: ExtractionResult(
            status="NEED_PARSER",
            source_type="discovery",
            message="Discovery did not find the requested value.",
        ),
    )
    monkeypatch.setitem(
        router.EXTRACTORS,
        "spa",
        lambda *_args: ExtractionResult(
            status="NEED_PARSER",
            source_type="discovery",
            message="Discovery did not find the requested value.",
        ),
    )
    monkeypatch.setattr(
        router,
        "try_legacy_source",
        lambda **kwargs: calls.update(kwargs) or brin_result,
    )
    monkeypatch.setattr(
        "sources.generic_auto_extractor.try_generic_auto",
        lambda **_kwargs: generic_result,
    )

    result = router.scrape_source_auto(
        url=url,
        tahun=2021,
        description="Indeks Daya Saing Daerah (IDSD)",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Indeks Daya Saing Daerah (IDSD)",
    )

    assert result is brin_result
    assert calls["tahun"] == 2021


def test_router_uses_brin_adapter_before_generic_discovery(monkeypatch):
    from engine import router
    from engine.detector import SourcePayload

    url = "https://brin.go.id/drid/page/indeks-daya-saing-daerah"
    brin_result = ExtractionResult(
        status="DATA_TIDAK_ADA",
        source_type="legacy:brin",
        message="Seri IDSD dimulai tahun 2022.",
    )
    calls = {}

    monkeypatch.setattr(
        router,
        "fetch_and_detect",
        lambda _url: SourcePayload(
            url=url,
            final_url=url,
            kind="spa",
            content=b"<html>BRIN</html>",
            content_type="text/html",
        ),
    )
    monkeypatch.setattr(
        router,
        "try_legacy_source",
        lambda **kwargs: calls.update(kwargs) or brin_result,
    )
    monkeypatch.setitem(
        router.EXTRACTORS,
        "spa",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("BRIN adapter should run before discovery.")
        ),
    )

    result = router.scrape_source_auto(
        url=url,
        tahun=2021,
        description="Indeks Daya Saing Daerah (IDSD)",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Indeks Daya Saing Daerah (IDSD)",
    )

    assert result is brin_result
    assert calls["tahun"] == 2021


def test_router_stops_after_search_listing_has_no_pdf(monkeypatch):
    from engine import router
    from engine.detector import SourcePayload

    monkeypatch.setattr(router, "try_legacy_source", lambda **_kwargs: None)
    monkeypatch.setattr(
        router,
        "fetch_and_detect",
        lambda url: SourcePayload(
            url=url,
            final_url=url,
            kind="html",
            content=b"<html>search listing</html>",
            content_type="text/html",
        ),
    )
    monkeypatch.setattr(router, "_try_bps_embedded_json", lambda *_args: None)
    monkeypatch.setattr(router, "_try_bps_next_data", lambda *_args: None)
    monkeypatch.setattr(
        bps_pdf_adapter,
        "try_bps_pdf",
        lambda **_kwargs: ExtractionResult(
            status="NEED_PARSER",
            source_type="bps_pdf",
            message="No PDF links",
            evidence=["pdf_candidates=0"],
        ),
    )
    monkeypatch.setattr(
        router,
        "extract_discovery",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("generic discovery should not re-scan a listing")
        ),
    )
    monkeypatch.setattr(
        "sources.generic_auto_extractor.try_generic_auto",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("generic extraction should not re-scan a listing")
        ),
    )

    result = router.scrape_source_auto(
        url="https://kalsel.bps.go.id/id/pressrelease?page=1&keyword=IKG",
        tahun=2024,
        description="Indeks Ketimpangan Gender",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Indeks Ketimpangan Gender (IKG)",
    )

    assert result.source_type == "bps_pdf"
    assert result.evidence == ["pdf_candidates=0"]


def test_router_preserves_bps_table_catalog_failure_diagnostics(monkeypatch):
    from engine import router
    from engine.detector import SourcePayload
    from sources import bps_table_adapter

    table_result = ExtractionResult(
        status="NEED_PARSER",
        source_type="bps_table_catalog",
        message=(
            "Katalog BPS termuat, tetapi baris tabel tidak memiliki teks. "
            "Data daftar dinamis mungkin gagal dimuat."
        ),
        evidence=["catalog_page=true", "catalog_rows_with_text=0"],
    )
    monkeypatch.setattr(router, "try_legacy_source", lambda **_kwargs: None)
    monkeypatch.setattr(
        router,
        "fetch_and_detect",
        lambda _url: SourcePayload(
            url="https://kalsel.bps.go.id/id/statistics-table?subject=531",
            final_url="https://kalsel.bps.go.id/id/statistics-table?subject=531",
            kind="html",
            content=b"<html>Daftar tabel</html>",
            content_type="text/html",
        ),
    )
    monkeypatch.setattr(router, "_try_bps_embedded_json", lambda *_args: None)
    monkeypatch.setattr(router, "_try_bps_next_data", lambda *_args: None)
    monkeypatch.setattr(
        bps_table_adapter,
        "try_bps_table",
        lambda **_kwargs: table_result,
    )
    monkeypatch.setattr(bps_pdf_adapter, "try_bps_pdf", lambda **_kwargs: None)
    monkeypatch.setitem(
        router.EXTRACTORS,
        "html",
        lambda *_args: ExtractionResult(
            status="NEED_PARSER",
            source_type="html",
            message="HTML tidak punya tabel langsung.",
        ),
    )
    monkeypatch.setattr(
        router,
        "extract_discovery",
        lambda *_args: ExtractionResult(
            status="NEED_PARSER",
            source_type="discovery",
            message="Discovery tidak menemukan data.",
        ),
    )
    monkeypatch.setattr(
        "sources.generic_auto_extractor.try_generic_auto",
        lambda **_kwargs: None,
    )

    result = router.scrape_source_auto(
        url="https://kalsel.bps.go.id/id/statistics-table?subject=531",
        tahun=2025,
        description="Kode 2.d | Pertumbuhan Ekonomi (%)",
        wilayah="Prov. Kalimantan Selatan",
        kode="2.d",
        nama_indikator="Pertumbuhan Ekonomi (%)",
    )

    assert result.source_type == "bps_table_catalog"
    assert "BPS TABLE:" in result.message
    assert "baris tabel tidak memiliki teks" in result.message
    assert "catalog_rows_with_text=0" in result.evidence


def test_find_pdf_links_resolves_relative_bps_downloads():
    html = (
        '<a href="/download.php?f=ikg.pdf">Unduh</a>'
        '<iframe src="../files/table.pdf"></iframe>'
    )

    assert bps_pdf_adapter._find_pdf_links(
        html,
        "https://kalsel.bps.go.id/id/pressrelease/",
    ) == [
        "https://kalsel.bps.go.id/download.php?f=ikg.pdf",
        "https://kalsel.bps.go.id/id/files/table.pdf",
    ]


def test_find_pdf_links_detects_generic_download_link_and_script_url():
    html = (
        '<a href="/files/report/123" title="Unduh PDF">Dokumen</a>'
        '<script>const file="//webapi.bps.go.id/download.php?f=abc123";</script>'
    )

    assert bps_pdf_adapter._find_pdf_links(
        html,
        "https://kalsel.bps.go.id/id/subject/",
    ) == [
        "https://kalsel.bps.go.id/files/report/123",
        "https://webapi.bps.go.id/download.php?f=abc123",
    ]


def test_article_discovery_prioritizes_matching_metric_and_year():
    html = (
        '<a href="/id/pressrelease/ikg-2024.html">'
        "Indeks Ketimpangan Gender 2024</a>"
        '<a href="/id/pressrelease/kemiskinan-2024.html">'
        "Profil Kemiskinan 2024</a>"
    )

    articles = bps_pdf_adapter._article_links(
        html,
        "https://kalsel.bps.go.id/id/pressrelease",
        "Indeks Ketimpangan Gender",
        2024,
    )

    assert len(articles) == 1
    assert "ikg-2024" in articles[0]["url"]


def test_article_discovery_ignores_partial_word_and_single_term_matches():
    html = (
        '<a href="/id/pressrelease/ikg-2024.html">'
        "Indeks Ketimpangan Gender (IKG) 2024</a>"
        '<a href="/id/pressrelease/gini-2024.html">'
        "Tingkat Ketimpangan Pengeluaran Penduduk 2024</a>"
        '<a href="/id/pressrelease/ihk-2024.html">'
        "Perkembangan Indeks Harga Konsumen 2024</a>"
    )

    articles = bps_pdf_adapter._article_links(
        html,
        "https://kalsel.bps.go.id/id/pressrelease",
        "Kode 40 | Indeks Ketimpangan Gender (IKG)",
        2024,
    )

    assert len(articles) == 1
    assert "ikg-2024" in articles[0]["url"]


def test_rendered_page_target_detection_waits_for_matching_listing_article():
    html = (
        '<a href="/id/pressrelease/2025/ikg-2024.html">'
        "Indeks Ketimpangan Gender (IKG) Provinsi Kalimantan Selatan 2024"
        "</a>"
    )

    assert bps_pdf_adapter._rendered_page_has_targets(
        html,
        "https://kalsel.bps.go.id/id/pressrelease?page=1",
        "Indeks Ketimpangan Gender (IKG)",
        2024,
    )
    assert not bps_pdf_adapter._rendered_page_has_targets(
        "<html>Loading...</html>",
        "https://kalsel.bps.go.id/id/pressrelease?page=1",
        "Indeks Ketimpangan Gender (IKG)",
        2024,
    )
    assert not bps_pdf_adapter._rendered_page_has_targets(
        '<a href="/id/pressrelease/ikg-related.html">'
        "Indeks Ketimpangan Gender terkait</a>",
        "https://kalsel.bps.go.id/id/pressrelease/ikg-2024.html",
        "Indeks Ketimpangan Gender (IKG)",
        2024,
        require_pdf=True,
    )


def test_article_discovery_finds_matching_non_pressrelease_pages():
    html = (
        '<a href="/id/publication/2024/03/ekonomi-kalsel.html">'
        "Pertumbuhan Ekonomi Kalimantan Selatan</a>"
        '<a href="/id/subject/2024/kemiskinan.html">Kemiskinan</a>'
    )

    articles = bps_pdf_adapter._article_links(
        html,
        "https://kalsel.bps.go.id/id/subject/",
        "Pertumbuhan Ekonomi",
        2024,
    )

    assert len(articles) == 1
    assert "/publication/" in articles[0]["url"]


def test_pdf_discovery_follows_relevant_bps_page_from_general_source(
    monkeypatch,
):
    source_url = "https://kalsel.bps.go.id/id/subject/"
    article_url = (
        "https://kalsel.bps.go.id/id/publication/ekonomi-kalsel.html"
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_html",
        lambda url, **_kwargs: (
            '<a href="https://webapi.bps.go.id/download.php?f=growth">'
            "Unduh PDF</a>"
            if url == article_url
            else ""
        ),
    )

    pdf_urls = bps_pdf_adapter._find_pdf_urls(
        source_url,
        (
            '<a href="/id/publication/ekonomi-kalsel.html">'
            "Pertumbuhan Ekonomi Kalimantan Selatan</a>"
        ),
        "Pertumbuhan Ekonomi",
        2024,
    )

    assert pdf_urls == [
        "https://webapi.bps.go.id/download.php?f=growth"
    ]


def test_pdf_discovery_paginates_search_and_follows_download_articles(
    monkeypatch,
):
    listing_url = (
        "https://kalsel.bps.go.id/id/pressrelease?"
        "page=1&keyword=Indeks+Ketimpangan+Gender"
    )
    article_2024 = (
        "https://kalsel.bps.go.id/id/pressrelease/2025/"
        "ikg-2024.html"
    )
    article_2023 = (
        "https://kalsel.bps.go.id/id/pressrelease/2024/"
        "ikg-2023.html"
    )
    page_two_url = bps_pdf_adapter._page_with_number(listing_url, 2)

    def fake_download(url, **_kwargs):
        if url == page_two_url:
            return (
                f'<a href="{article_2023}">'
                "Indeks Ketimpangan Gender 2023</a>"
            )
        if url == article_2024:
            return (
                '<a class="download-product" '
                'href="https://webapi.bps.go.id/download.php?f=ikg2024">'
                "Unduh Berita Resmi Statistik</a>"
            )
        if url == article_2023:
            return (
                '<a class="download-product" '
                'href="https://webapi.bps.go.id/download.php?f=ikg2023">'
                "Unduh Berita Resmi Statistik</a>"
            )
        if "page=3" in url:
            return "<html>no more results</html>"
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_html",
        fake_download,
    )

    pdf_urls = bps_pdf_adapter._find_pdf_urls(
        listing_url,
        f'<a href="{article_2024}">'
        "Indeks Ketimpangan Gender (IKG) Kalimantan Selatan 2024</a>",
        "Indeks Ketimpangan Gender",
        2024,
    )

    assert pdf_urls == [
        "https://webapi.bps.go.id/download.php?f=ikg2024",
        "https://webapi.bps.go.id/download.php?f=ikg2023",
    ]
    assert bps_pdf_adapter._PDF_REFERER_CACHE[pdf_urls[0]] == article_2024
    assert bps_pdf_adapter._PDF_REFERER_CACHE[pdf_urls[1]] == article_2023


def test_pdf_discovery_renders_article_when_download_link_is_client_side(
    monkeypatch,
):
    listing_url = (
        "https://kalsel.bps.go.id/id/pressrelease?"
        "page=1&keyword=Indeks+Ketimpangan+Gender"
    )
    article_url = (
        "https://kalsel.bps.go.id/id/pressrelease/2025/"
        "ikg-2024.html"
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_html",
        lambda *_args, **_kwargs: (
            f'<a href="{article_url}">'
            "Indeks Ketimpangan Gender (IKG) 2024</a>"
            if _args[0] == listing_url
            else "<html><script>self.__next_f.push([])</script></html>"
        ),
    )

    rendered = []

    def fake_render(url, _context, _year):
        rendered.append(url)
        return (
            '<a class="download-product" '
            'href="https://webapi.bps.go.id/download.php?f=ikg2024">'
            "Unduh Berita Resmi Statistik</a>",
            [],
        )

    monkeypatch.setattr(bps_pdf_adapter, "_render_page", fake_render)

    pdf_urls = bps_pdf_adapter._find_pdf_urls(
        listing_url,
        (
            f'<a href="{article_url}">'
            "Indeks Ketimpangan Gender (IKG) 2024</a>"
        ),
        "Indeks Ketimpangan Gender",
        2024,
    )

    assert rendered == [article_url]
    assert pdf_urls == [
        "https://webapi.bps.go.id/download.php?f=ikg2024"
    ]


def test_pdf_candidates_are_reused_across_requested_years(monkeypatch):
    source_url = "https://kalsel.bps.go.id/id/pressrelease?keyword=example"
    discovery_calls = []
    download_calls = []
    monkeypatch.setattr(bps_pdf_adapter, "_RESULT_CACHE", {})
    monkeypatch.setattr(bps_pdf_adapter, "_PDF_URL_CACHE", {})
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_find_pdf_urls",
        lambda *_args: (
            discovery_calls.append(True)
            or ["https://webapi.bps.go.id/download.php?f=report"]
        ),
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_pdf",
        lambda url, **_kwargs: download_calls.append(url) or b"%PDF-test",
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_extract_from_downloaded_pdf",
        lambda _url, _content, instruction, year: ExtractionResult(
            status="SUCCESS",
            value={instruction.target_wilayahs[0]: float(year)},
            confidence=0.9,
            source_type="pdf",
        ),
    )

    results = [
        bps_pdf_adapter.try_bps_pdf(
            source_url,
            year,
            nama_indikator="Contoh Indikator",
            wilayah="Prov. Kalimantan Selatan",
            page_html="<html></html>",
        )
        for year in (2023, 2024)
    ]

    assert len(discovery_calls) == 1
    assert download_calls == ["https://webapi.bps.go.id/download.php?f=report"]
    assert [result.value for result in results] == [
        {"Prov. Kalimantan Selatan": 2023.0},
        {"Prov. Kalimantan Selatan": 2024.0},
    ]


def test_connection_failure_is_cached_per_url_across_same_host_candidates(
    monkeypatch,
):
    source_url = "https://kalsel.bps.go.id/id/pressrelease?keyword=example"
    pdf_urls = [
        f"https://webapi.bps.go.id/download.php?f=report{index}"
        for index in range(3)
    ]
    download_calls = []
    monkeypatch.setattr(bps_pdf_adapter, "_RESULT_CACHE", {})
    monkeypatch.setattr(bps_pdf_adapter, "_PDF_URL_CACHE", {})
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_find_pdf_urls",
        lambda *_args: pdf_urls,
    )

    def fail_download(url, **_kwargs):
        download_calls.append(url)
        raise RuntimeError(
            "requests: ConnectionError: ConnectionReset; "
            "urllib: URLError: connection reset"
        )

    monkeypatch.setattr(bps_pdf_adapter, "download_pdf", fail_download)
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_download_pdf_via_browser",
        lambda _url, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("browser download failed")
        ),
    )

    results = [
        bps_pdf_adapter.try_bps_pdf(
            source_url,
            year,
            nama_indikator="Contoh Indikator",
            wilayah="Prov. Kalimantan Selatan",
            page_html="<html></html>",
        )
        for year in (2023, 2024)
    ]

    assert download_calls == pdf_urls
    assert all(result.status == "NEED_PARSER" for result in results)
    assert "ConnectionError" in results[0].message
    assert "dilewati karena koneksi" not in results[0].message


def test_http_download_failure_falls_back_to_browser(monkeypatch):
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_pdf",
        lambda _url, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("requests and urllib connection reset")
        ),
    )
    browser_calls = []
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_download_pdf_via_browser",
        lambda url, **kwargs: browser_calls.append(
            (url, kwargs.get("referer"))
        ) or b"%PDF-browser",
    )

    content = bps_pdf_adapter._download_pdf(
        "https://webapi.bps.go.id/download.php?f=ikg",
        referer="https://kalsel.bps.go.id/id/pressrelease/ikg.html",
    )

    assert content == b"%PDF-browser"
    assert browser_calls == [
        (
            "https://webapi.bps.go.id/download.php?f=ikg",
            "https://kalsel.bps.go.id/id/pressrelease/ikg.html",
        )
    ]


def test_http_pdf_download_uses_legacy_minimal_headers(monkeypatch):
    requests_seen = {}

    class FakeResponse:
        content = b"%PDF-response"

        def raise_for_status(self):
            pass

    def fake_get(url, headers, timeout):
        requests_seen.update(
            {"url": url, "headers": headers, "timeout": timeout}
        )
        return FakeResponse()

    monkeypatch.setattr(bps_utils.requests, "get", fake_get)
    monkeypatch.setattr(
        bps_utils.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("8.8.8.8", 443))
        ],
    )

    content = bps_utils.download_pdf(
        "https://webapi.bps.go.id/download.php?f=ikg",
        referer="https://kalsel.bps.go.id/id/pressrelease/ikg.html",
    )

    assert content == b"%PDF-response"
    assert requests_seen["headers"] == {
        "User-Agent": "Mozilla/5.0",
    }


def test_http_pdf_download_retries_with_article_referer(monkeypatch):
    seen_headers = []
    urllib_calls = []

    class FakeResponse:
        content = b"%PDF-response"

        def raise_for_status(self):
            pass

    def fake_get(_url, headers, timeout):
        seen_headers.append(dict(headers))
        if "Referer" not in headers:
            raise RuntimeError("referer required")
        return FakeResponse()

    def fake_urlopen(request, timeout):
        urllib_calls.append((dict(request.header_items()), timeout))
        raise RuntimeError("request failed")

    monkeypatch.setattr(bps_utils.requests, "get", fake_get)
    monkeypatch.setattr(bps_utils.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        bps_utils.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("8.8.8.8", 443))
        ],
    )

    content = bps_utils.download_pdf(
        "https://webapi.bps.go.id/download.php?f=ikg",
        referer="https://kalsel.bps.go.id/id/pressrelease/ikg.html",
    )

    assert content == b"%PDF-response"
    assert seen_headers == [
        {"User-Agent": "Mozilla/5.0"},
        {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://kalsel.bps.go.id/id/pressrelease/ikg.html",
        },
    ]
    assert len(urllib_calls) == 1


def test_http_pdf_download_uses_public_dns_with_original_tls_host(monkeypatch):
    pool_calls = []
    dns_calls = []

    class FakeDnsResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "Answer": [
                    {"type": 1, "data": "8.8.8.8"},
                    {"type": 5, "data": "webapi.vs.bps.go.id"},
                ]
            }

    class FakePdfResponse:
        status = 200
        data = b"%PDF-public-dns"

    class FakePool:
        def __init__(self, host, **kwargs):
            pool_calls.append({"host": host, **kwargs})

        def urlopen(self, method, path, **kwargs):
            pool_calls[-1].update(
                {"method": method, "path": path, **kwargs}
            )
            return FakePdfResponse()

        def close(self):
            pass

    def fake_get(url, **kwargs):
        if url == "https://dns.google/resolve":
            dns_calls.append(kwargs)
            return FakeDnsResponse()
        raise bps_utils.requests.ConnectionError("connection reset")

    monkeypatch.setattr(bps_utils.requests, "get", fake_get)
    monkeypatch.setattr(
        bps_utils.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("connection reset")
        ),
    )
    monkeypatch.setattr(
        bps_utils.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("10.0.11.232", 443))
        ],
    )
    monkeypatch.setattr(bps_utils.urllib3, "HTTPSConnectionPool", FakePool)

    content = bps_utils.download_pdf(
        "https://webapi.bps.go.id/download.php?f=ikg",
        referer="https://kalsel.bps.go.id/id/pressrelease/ikg.html",
    )

    assert content == b"%PDF-public-dns"
    assert len(dns_calls) == 1
    assert pool_calls[0]["host"] == "8.8.8.8"
    assert pool_calls[0]["server_hostname"] == "webapi.bps.go.id"
    assert pool_calls[0]["assert_hostname"] == "webapi.bps.go.id"
    assert pool_calls[0]["method"] == "GET"
    assert pool_calls[0]["path"] == "/download.php?f=ikg"
    assert pool_calls[0]["headers"]["Host"] == "webapi.bps.go.id"
    assert (
        pool_calls[0]["headers"]["Referer"]
        == "https://kalsel.bps.go.id/id/pressrelease/ikg.html"
    )


def test_http_pdf_download_skips_public_dns_when_system_dns_is_public(
    monkeypatch,
):
    dns_calls = []

    def fake_get(url, **kwargs):
        if url == "https://dns.google/resolve":
            dns_calls.append(url)
            raise AssertionError("DoH must not be used")
        raise bps_utils.requests.ConnectionError("connection reset")

    monkeypatch.setattr(bps_utils.requests, "get", fake_get)
    monkeypatch.setattr(
        bps_utils.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("connection reset")
        ),
    )
    monkeypatch.setattr(
        bps_utils.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("8.8.8.8", 443))
        ],
    )

    with pytest.raises(RuntimeError, match="Gagal mengunduh PDF"):
        bps_utils.download_pdf(
            "https://webapi.bps.go.id/download.php?f=ikg"
        )

    assert dns_calls == []


def test_browser_pdf_fallback_uses_visible_chrome_and_article_page(
    monkeypatch,
):
    from selenium import webdriver

    calls = {"visited": [], "arguments": []}

    class FakeDriver:
        def set_page_load_timeout(self, _timeout):
            pass

        def execute_cdp_cmd(self, _command, params):
            calls["download_dir"] = params["downloadPath"]

        def get(self, url):
            calls["visited"].append(url)
            if url.endswith("download.php?f=ikg"):
                output_path = (
                    bps_pdf_adapter.Path(calls["download_dir"])
                    / "ikg.pdf"
                )
                output_path.write_bytes(b"%PDF-browser")

        def execute_script(self, *_args):
            return None

        def quit(self):
            pass

    def fake_chrome(options):
        calls["arguments"] = options.arguments
        return FakeDriver()

    monkeypatch.setattr(webdriver, "Chrome", fake_chrome)

    content = bps_pdf_adapter._download_pdf_via_browser(
        "https://webapi.bps.go.id/download.php?f=ikg",
        referer="https://kalsel.bps.go.id/id/pressrelease/ikg.html",
    )

    assert content == b"%PDF-browser"
    assert calls["visited"] == [
        "https://kalsel.bps.go.id/id/pressrelease/ikg.html",
        "https://webapi.bps.go.id/download.php?f=ikg",
    ]
    assert not any(
        argument.startswith("--headless")
        for argument in calls["arguments"]
    )


def test_browser_download_reports_stalled_partial_file(monkeypatch):
    from selenium import webdriver

    calls = {}

    class FakeDriver:
        def set_page_load_timeout(self, _timeout):
            pass

        def execute_cdp_cmd(self, _command, params):
            calls["download_dir"] = params["downloadPath"]

        def get(self, _url):
            partial = (
                bps_pdf_adapter.Path(calls["download_dir"])
                / "report.pdf.crdownload"
            )
            partial.write_bytes(b"%PDF-partial")

        def quit(self):
            pass

    monkeypatch.setattr(webdriver, "Chrome", lambda **_kwargs: FakeDriver())
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_BROWSER_DOWNLOAD_IDLE_TIMEOUT",
        0.01,
    )

    with pytest.raises(RuntimeError, match="macet tanpa progres"):
        bps_pdf_adapter._download_pdf_via_browser(
            "https://webapi.bps.go.id/download.php?f=ikg"
        )


def test_direct_pdf_delegates_content_to_shared_pdf_extractor(monkeypatch):
    monkeypatch.setattr(bps_pdf_adapter, "_RESULT_CACHE", {})
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_pdf",
        lambda _url, **_kwargs: b"%PDF-test",
    )
    extracted = {}

    def fake_extract(payload, instruction, year):
        extracted["kind"] = payload.kind
        extracted["content"] = payload.content
        extracted["instruction"] = instruction
        extracted["year"] = year
        return ExtractionResult(
            status="SUCCESS",
            value=0.483,
            confidence=0.91,
            source_type="pdf",
            message="Fixture extraction",
        )

    monkeypatch.setattr(bps_pdf_adapter, "extract_pdf", fake_extract)

    result = bps_pdf_adapter.try_bps_pdf(
        "https://kalsel.bps.go.id/files/indikator.pdf",
        2024,
        description="Ambil nilai untuk Kab. Tanah Laut",
        wilayah="Kab. Tanah Laut",
        nama_indikator="Indeks Ketimpangan Gender",
    )

    assert result.status == "SUCCESS"
    assert result.value == 0.483
    assert extracted["kind"] == "pdf"
    assert extracted["content"] == b"%PDF-test"
    assert extracted["year"] == 2024
    assert "gender" in extracted["instruction"].metric_terms


def test_html_page_uses_supplied_payload_and_extracts_linked_pdf(monkeypatch):
    monkeypatch.setattr(bps_pdf_adapter, "_RESULT_CACHE", {})
    downloaded_urls = []
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_pdf",
        lambda url, **_kwargs: downloaded_urls.append(url) or b"%PDF-test",
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_html",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provided HTML must be reused")
        ),
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "extract_pdf",
        lambda *_args: ExtractionResult(
            status="SUCCESS",
            value={"Kab. Tanah Laut": 0.483},
            confidence=0.90,
            source_type="pdf",
        ),
    )

    result = bps_pdf_adapter.try_bps_pdf(
        "https://kalsel.bps.go.id/id/pressrelease/ikg.html",
        2024,
        description="Ambil IKG Kab. Tanah Laut",
        wilayah="Kab. Tanah Laut",
        page_html=(
            '<a href="/download.php?f=ikg.pdf">Unduh PDF</a>'
        ),
    )

    assert result.status == "SUCCESS"
    assert downloaded_urls == [
        "https://kalsel.bps.go.id/download.php?f=ikg.pdf"
    ]


def test_multi_pdf_results_mark_incomplete_region_set_for_review():
    result = bps_pdf_adapter._merge_pdf_results(
        [
            (
                "https://bps.go.id/a.pdf",
                ExtractionResult(
                    status="SUCCESS",
                    value={"Kab. A": 1.0},
                    confidence=0.9,
                    source_type="pdf",
                ),
            )
        ],
        ["Kab. A", "Kab. B"],
    )

    assert result.status == "REVIEW"
    assert result.value == {"Kab. A": 1.0}
    assert "Belum ditemukan 1" in result.message


def test_non_bps_url_is_not_handled_by_bps_connector():
    assert bps_pdf_adapter.try_bps_pdf(
        "https://example.com/data.pdf",
        2024,
    ) is None


def test_stops_after_high_confidence_pdf_result(monkeypatch):
    monkeypatch.setattr(bps_pdf_adapter, "_RESULT_CACHE", {})
    monkeypatch.setattr(bps_pdf_adapter, "_PDF_URL_CACHE", {})
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_find_pdf_urls",
        lambda *_args: ["https://bps.go.id/a.pdf", "https://bps.go.id/b.pdf"],
    )
    downloaded_urls = []
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_pdf",
        lambda url, **_kwargs: downloaded_urls.append(url) or b"%PDF-test",
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_extract_from_downloaded_pdf",
        lambda *_args: ExtractionResult(
            status="SUCCESS",
            value=5.1,
            confidence=0.90,
            source_type="pdf",
        ),
    )

    result = bps_pdf_adapter.try_bps_pdf(
        "https://kalsel.bps.go.id/id/pressrelease/",
        2024,
        description="Pertumbuhan ekonomi Prov. Kalimantan Selatan",
        wilayah="Prov. Kalimantan Selatan",
        nama_indikator="Pertumbuhan Ekonomi",
        page_html="",
    )

    assert result.status == "SUCCESS"
    assert downloaded_urls == ["https://bps.go.id/a.pdf"]


def test_failed_download_reason_is_returned_without_pdf_token(monkeypatch):
    monkeypatch.setattr(bps_pdf_adapter, "_RESULT_CACHE", {})
    monkeypatch.setattr(bps_pdf_adapter, "_PDF_URL_CACHE", {})
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_find_pdf_urls",
        lambda *_args: [
            "https://webapi.bps.go.id/download.php?f=private-token"
        ],
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "download_pdf",
        lambda _url, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("requests: Timeout; urllib: ConnectionReset")
        ),
    )
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_download_pdf_via_browser",
        lambda _url, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("browser download failed")
        ),
    )

    result = bps_pdf_adapter.try_bps_pdf(
        "https://kalsel.bps.go.id/id/pressrelease/",
        2024,
        nama_indikator="Pertumbuhan Ekonomi",
        wilayah="Prov. Kalimantan Selatan",
        page_html="",
    )

    assert result.status == "NEED_PARSER"
    assert "Timeout" in result.message
    assert "ConnectionReset" in result.message
    assert "private-token" not in result.message


def test_missing_pdf_link_is_reported_as_completed_discovery(monkeypatch):
    monkeypatch.setattr(bps_pdf_adapter, "_RESULT_CACHE", {})
    monkeypatch.setattr(bps_pdf_adapter, "_PDF_URL_CACHE", {})
    monkeypatch.setattr(
        bps_pdf_adapter,
        "_find_pdf_urls",
        lambda *_args: [],
    )

    result = bps_pdf_adapter.try_bps_pdf(
        "https://kalsel.bps.go.id/id/pressrelease/",
        2024,
        nama_indikator="Indeks Ketimpangan Gender",
        page_html="<html></html>",
    )

    assert result.status == "NEED_PARSER"
    assert result.evidence == ["pdf_candidates=0"]
