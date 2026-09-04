import json

import pandas as pd

from engine.tabular import extract_from_dataframes


def _collect_frames(value, frames):
    if isinstance(value, list):
        if value and all(isinstance(x, dict) for x in value):
            try:
                frames.append(pd.json_normalize(value))
            except Exception:
                pass
        for item in value[:50]:
            _collect_frames(item, frames)

    elif isinstance(value, dict):
        try:
            frames.append(pd.json_normalize(value))
        except Exception:
            pass
        for item in value.values():
            _collect_frames(item, frames)


def extract_json(payload, instruction, tahun):
    data = json.loads(payload.content.decode("utf-8"))
    frames = []
    _collect_frames(data, frames)

    return extract_from_dataframes(
        frames,
        instruction,
        tahun,
        source_type="json",
    )
