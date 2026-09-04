from io import BytesIO

import pandas as pd

from engine.tabular import extract_from_dataframes


def extract_excel(payload, instruction, tahun):
    book = pd.ExcelFile(BytesIO(payload.content))
    frames = []

    for sheet in book.sheet_names:
        try:
            # beberapa publikasi punya header bertingkat/berantakan;
            # coba beberapa posisi header.
            for header in [0, 1, 2, 3, 4]:
                df = pd.read_excel(
                    BytesIO(payload.content),
                    sheet_name=sheet,
                    header=header,
                )
                if not df.empty and len(df.columns) >= 2:
                    frames.append(df)
        except Exception:
            continue

    return extract_from_dataframes(
        frames,
        instruction,
        tahun,
        source_type="excel",
    )
