from sources import stunting_source


def test_get_pdf_bytes_downloads_duplicate_pdf_url_once(monkeypatch):
    source_url = "https://example.test/ssgi"
    pdf_url = (
        "https://drive.google.com/uc?export=download&id=ssgi-2024"
    )
    download_calls = []

    stunting_source._PDF_CACHE.clear()
    monkeypatch.setattr(
        stunting_source,
        "_split_source_urls",
        lambda _url: ["source-1", "source-2"],
    )
    monkeypatch.setattr(
        stunting_source,
        "find_pdf_links",
        lambda _url: [pdf_url],
    )
    monkeypatch.setattr(
        stunting_source,
        "_download_pdf",
        lambda url: download_calls.append(url) or b"%PDF test",
    )
    monkeypatch.setattr(
        stunting_source,
        "_detect_report_year",
        lambda _content: 2024,
    )

    try:
        pdfs = stunting_source.get_pdf_bytes(source_url)
    finally:
        stunting_source._PDF_CACHE.clear()

    assert pdfs == {2024: b"%PDF test"}
    assert download_calls == [pdf_url]
