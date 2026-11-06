from engine.instructions import parse_instruction
from extractors import browser


def test_extract_browser_parses_bps_comma_decimal(monkeypatch):
    monkeypatch.setattr(
        browser,
        "render_page",
        lambda _url, **_kwargs: """
        <table>
          <tr><th>Provinsi</th>
              <th>Indeks Demokrasi Indonesia 2025</th></tr>
          <tr><td>KALIMANTAN SELATAN</td><td>81,23</td></tr>
        </table>
        """,
    )
    instruction = parse_instruction(
        "Indeks Demokrasi Indonesia (IDI)",
        default_wilayah="Prov. Kalimantan Selatan",
    )

    result = browser.extract_browser(
        "https://kalsel.bps.go.id/id/statistics-table/2/table/idi.html",
        instruction,
        2025,
        decimal=",",
        thousands=".",
        select_year=2025,
    )

    assert result.status == "SUCCESS"
    assert result.value == 81.23
