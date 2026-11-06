import importlib
from pathlib import Path

import pytest

from sources.legacy_bridge import _error_result


@pytest.mark.parametrize(
    "error_text",
    [
        "NameResolutionError: Failed to resolve 'perpustakaan.bappenas.go.id'",
        (
            "SOURCE_DOWNLOAD_FAILED: Chrome gagal mengunduh PDF; "
            "tidak ada respons HTTP 403/429 yang teramati."
        ),
    ],
)
def test_ibei_network_failure_is_not_classified_as_blocked(error_text):
    result = _error_result(RuntimeError(error_text), "ibei")

    assert result.status == "LEGACY_FAILED"
    assert "SOURCE_TERBLOKIR" not in result.message


def test_browser_download_marks_blocked_only_after_forbidden_response(
    monkeypatch,
):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "legacy_indicators")
    )
    browser_fetch = importlib.import_module("browser_fetch")

    class FakeDriver:
        def set_page_load_timeout(self, _timeout):
            pass

        def quit(self):
            pass

    monkeypatch.setattr(browser_fetch, "_create_driver", lambda: FakeDriver())
    monkeypatch.setattr(browser_fetch, "_hide_automation", lambda _driver: None)
    monkeypatch.setattr(
        browser_fetch,
        "_download_once",
        lambda *_args: (None, set()),
    )

    with pytest.raises(RuntimeError, match="SOURCE_DOWNLOAD_FAILED"):
        browser_fetch.download_pdf_via_browser(
            "https://perpustakaan.bappenas.go.id/report.pdf",
            attempts=1,
        )

    monkeypatch.setattr(
        browser_fetch,
        "_download_once",
        lambda *_args: (None, {403}),
    )

    with pytest.raises(RuntimeError, match="SOURCE_BLOCKED"):
        browser_fetch.download_pdf_via_browser(
            "https://perpustakaan.bappenas.go.id/report.pdf",
            attempts=1,
        )


def test_ibei_error_summary_preserves_dns_failure_as_unavailable_reason(
    monkeypatch,
):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "legacy_indicators")
    )
    ibei = importlib.import_module("ibei")

    message = ibei._build_final_pdf_error(
        [
            RuntimeError(
                "NameResolutionError: Failed to resolve "
                "'perpustakaan.bappenas.go.id'"
            ),
            RuntimeError(
                "SOURCE_DOWNLOAD_FAILED: no HTTP 403/429 response observed."
            ),
        ]
    )

    assert message.startswith("PDF IBEI gagal diperoleh")
    assert "SOURCE_BLOCKED" not in message


@pytest.mark.parametrize("status_code", [403, 429])
def test_ibei_search_preserves_explicit_block_status(
    monkeypatch,
    status_code,
):
    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[1] / "legacy_indicators")
    )
    ibei = importlib.import_module("ibei")

    class FakeResponse:
        text = "<html>No PDF links</html>"

        def raise_for_status(self):
            error = ibei.requests.HTTPError(
                f"{status_code} Client Error"
            )
            error.response = type(
                "Response",
                (),
                {"status_code": status_code},
            )()
            raise error

    class FakeSession:
        def get(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(
        ibei,
        "render_html_via_browser",
        lambda *_args, **_kwargs: "<html>No PDF links</html>",
    )

    with pytest.raises(RuntimeError, match="SOURCE_BLOCKED") as exc_info:
        ibei.find_latest_ibei_pdf_url(FakeSession())

    result = _error_result(exc_info.value, "ibei")
    assert result.status == "SUMBER_TERBLOKIR"
