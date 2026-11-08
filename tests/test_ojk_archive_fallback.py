"""Dependency-light tests for the OJK archive row parser.

The project-wide OJK module imports Selenium and application utilities. These
unit tests compile only the pure parsing helpers so they can run without a
browser session.
"""
import ast
import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OJK_SOURCE = PROJECT_ROOT / "legacy_indicators" / "ojk.py"


@pytest.fixture(scope="module")
def parser_namespace():
    source = OJK_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {
        "parse_ojk_number",
        "_ojk_archive_number_tokens",
        "_ojk_find_province_total_asset_in_pdf_text",
    }
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    assert {node.name for node in nodes} == wanted
    namespace = {"re": re}
    exec(
        compile(ast.Module(body=nodes, type_ignores=[]), str(OJK_SOURCE), "exec"),
        namespace,
    )
    return namespace


def test_extract_total_asset_2020_row(parser_namespace):
    text = (
        "Kalimantan Selatan 1 1.104 240.645.932.539 "
        "240.515.333.441 236.607.462.499"
    )
    assert parser_namespace["_ojk_find_province_total_asset_in_pdf_text"](text) == 240645932539.0


def test_extract_total_asset_2021_row(parser_namespace):
    text = (
        "Kalimantan Selatan 1 1.219 265.775.525.361 "
        "265.690.040.237 262.044.769.562"
    )
    assert parser_namespace["_ojk_find_province_total_asset_in_pdf_text"](text) == 265775525361.0


def test_parser_does_not_return_net_asset_or_investment(parser_namespace):
    text = (
        "Kalimantan Selatan 1 1.219 265.775.525.361 "
        "265.690.040.237 262.044.769.562"
    )
    value = parser_namespace["_ojk_find_province_total_asset_in_pdf_text"](text)
    assert value != 265690040237.0
    assert value != 262044769562.0


def test_missing_province_returns_none(parser_namespace):
    text = "Kalimantan Tengah 1 698 89.690.145.171 89.510.284.671"
    assert parser_namespace["_ojk_find_province_total_asset_in_pdf_text"](text) is None
