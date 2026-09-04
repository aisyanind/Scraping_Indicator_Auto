from io import BytesIO, StringIO

import pandas as pd

from engine.tabular import extract_from_dataframes


def extract_csv(payload, instruction, tahun):
    text = payload.content.decode("utf-8-sig", errors="replace")
    frames = []

    for sep in [",", ";", "\t", "|"]:
        try:
            df = pd.read_csv(StringIO(text), sep=sep)
            if len(df.columns) >= 2:
                frames.append(df)
        except Exception:
            pass

    return extract_from_dataframes(
        frames,
        instruction,
        tahun,
        source_type="csv",
    )
