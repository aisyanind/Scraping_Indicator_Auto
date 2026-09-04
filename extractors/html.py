from io import StringIO

import pandas as pd

from engine.tabular import extract_from_dataframes


def extract_html(payload, instruction, tahun):
    html = payload.content.decode("utf-8", errors="replace")
    try:
        frames = pd.read_html(StringIO(html))
    except Exception:
        frames = []

    return extract_from_dataframes(
        frames,
        instruction,
        tahun,
        source_type="html",
    )
