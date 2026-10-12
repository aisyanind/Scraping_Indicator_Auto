import re
from typing import Iterable

import pandas as pd

from models import ExtractionResult
from engine.instructions import Instruction
from sources.bps_utils import METRIC_SYNONYMS
from sources.region_utils import normalize_region, normalize_text, REGION_ALIASES


REGION_HINTS = {
    "wilayah", "provinsi", "province", "kabupaten", "kab", "kota",
    "kab/kota", "kabupaten/kota", "daerah", "dati1", "dati2",
}
YEAR_HINTS = {"tahun", "year", "periode", "period"}


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    cols = []
    seen = {}
    for col in df.columns:
        if isinstance(col, tuple):
            text = " ".join(
                str(x) for x in col if str(x).lower() != "nan"
            )
        else:
            text = str(col)
        normalized = normalize_text(text)

        # Hindari nama kolom duplikat (df[col] jadi DataFrame).
        if normalized in seen:
            seen[normalized] += 1
            normalized = f"{normalized}__{seen[normalized]}"
        else:
            seen[normalized] = 1

        cols.append(normalized)
    df.columns = cols
    return df


def _find_columns(df: pd.DataFrame, hints: set[str]) -> list[str]:
    matches = []
    for col in df.columns:
        c = normalize_text(col)
        if any(hint in c for hint in hints):
            matches.append(col)
    return matches


def _find_region_columns(
    df: pd.DataFrame,
    instruction: "Instruction",
) -> list[str]:
    """
    Selain kolom berisi hint wilayah, deteksi juga kolom yang header-nya
    adalah nama wilayah (format wide: tahun di baris, wilayah di kolom).
    """
    cols = _find_columns(df, REGION_HINTS)

    for col in df.columns:
        c = normalize_text(col)

        for target in instruction.target_wilayahs:
            bare = re.sub(
                r"^(prov\.|kab\.|kota)\s+",
                "",
                normalize_region(target),
            )
            if not bare:
                continue
            if bare == c or bare in c:
                if col not in cols:
                    cols.append(col)
                    break

    return cols


def _extract_year_from_column(col_name):
    text = normalize_text(col_name)
    m = re.search(r"\b(20\d{2})\b", text)
    if m:
        return int(m.group(1))
    return None


def _metric_score(column: str, terms: list[str]) -> float:
    c = normalize_text(column)
    if not terms:
        return 0.25

    hits = 0
    for term in terms:
        if term in c:
            hits += 1
            continue
        synonyms = METRIC_SYNONYMS.get(term, [])
        if any(syn in c for syn in synonyms):
            hits += 0.7

    return hits / max(len(terms), 1)


def _numeric_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce")

    def parse(v):
        text = str(v or "").strip()
        if not text:
            return None

        text = re.sub(r"[^0-9,.\-]", "", text)
        if not text:
            return None

        if "," in text and "." in text:
            if text.rfind(",") > text.rfind("."):
                text = text.replace(".", "").replace(",", ".")
            else:
                text = text.replace(",", "")
        elif "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif "." in text:
            text = text.replace(".", "")

        try:
            return float(text)
        except Exception:
            return None

    return series.map(parse)


def extract_from_dataframes(
    frames: Iterable[pd.DataFrame],
    instruction: Instruction,
    tahun: int,
    source_type: str,
) -> ExtractionResult:
    best = None

    for frame_index, raw_df in enumerate(frames):
        if raw_df is None or raw_df.empty:
            continue

        df = _flatten_columns(raw_df)
        region_cols = _find_region_columns(df, instruction)
        year_cols = _find_columns(df, YEAR_HINTS)

        # Kolom yang header-nya berupa tahun (format long: wilayah di baris,
        # tahun di kolom), mis. ["2019","2020","2021",...].
        year_header_cols = [
            col for col in df.columns
            if re.fullmatch(r"20\d{2}", normalize_text(col))
        ]

        numeric_candidates = []
        for col in df.columns:
            numeric = _numeric_series(df[col])
            ratio = numeric.notna().mean()
            if ratio >= 0.35:
                numeric_candidates.append(
                    (col, numeric, _metric_score(col, instruction.metric_terms))
                )

        if not numeric_candidates:
            continue

        # Prioritaskan kolom nilai: (1) kolom tahun == tahun yang diminta
        # (long format), (2) yang juga kolom wilayah (wide format),
        # (3) yang bukan kolom tahun, (4) metric score tinggi.
        def _candidate_rank(item):
            col = item[0]
            col_year = _extract_year_from_column(col)
            exact_year = 1 if col_year == int(tahun) else 0
            is_region = 1 if col in region_cols else 0
            is_year_header = 1 if col in year_header_cols else 0
            is_year = 1 if col in year_cols else 0
            year_penalty = -1 if col_year and col_year != int(tahun) else 0
            return (
                -exact_year,
                year_penalty,
                -is_region,
                is_year_header,
                is_year,
                -item[2],
                col,
            )

        numeric_candidates.sort(key=_candidate_rank)

        for value_col, numeric_series, metric_score in numeric_candidates:
            col_year = _extract_year_from_column(value_col)
            if col_year and col_year != int(tahun):
                continue

            work = df.copy()
            work["__value__"] = numeric_series

            year_conf = 0.0
            if year_cols:
                matched_year = pd.Series(False, index=work.index)
                for col in year_cols:
                    vals = work[col].astype(str).str.extract(
                        r"(\d{4})", expand=False
                    )
                    matched_year |= vals.eq(str(int(tahun)))
                if matched_year.any():
                    work = work[matched_year].copy()
                    year_conf = 0.20
                else:
                    continue

            region_values = {}
            region_conf = 0.0

            # Prioritas: baris "Total"/"Jumlah" untuk provinsi.
            total_row_value = None
            first_col = work.columns[0] if len(work.columns) > 0 else None
            if first_col is not None:
                first_col_norm = work[first_col].map(
                    lambda x: normalize_text(str(x or ""))
                )
                total_mask = first_col_norm.isin(["total", "jumlah"])
                if total_mask.any():
                    total_vals = work.loc[total_mask, "__value__"].dropna()
                    if not total_vals.empty:
                        total_row_value = float(total_vals.iloc[0])

            # Wide format: header kolom nilai adalah nama wilayah
            # (mis. kolom "KALIMANTAN SELATAN" berisi nilai per tahun).
            value_col_norm = normalize_text(value_col)
            wide_target = None

            for target in instruction.target_wilayahs:
                bare = re.sub(
                    r"^(prov\.|kab\.|kota)\s+",
                    "",
                    normalize_region(target),
                )
                if not bare:
                    continue
                if bare == value_col_norm or bare in value_col_norm:
                    wide_target = target
                    break

            if wide_target is not None:
                values = work["__value__"].dropna()
                if not values.empty:
                    region_values[wide_target] = float(values.iloc[0])
                    region_conf = 0.35
            elif region_cols:
                for target in instruction.target_wilayahs:
                    target_norm = normalize_region(target)
                    found_value = None

                    # Cek apakah target adalah provinsi (bukan kab/kota).
                    is_prov = target_norm.startswith("prov.")

                    # Jika provinsi dan ada Total row, pakai Total.
                    if is_prov and total_row_value is not None:
                        found_value = total_row_value

                    if found_value is None:
                        for col in region_cols:
                            normalized = work[col].map(normalize_region)
                            exact = work[normalized == target_norm]
                            if exact.empty:
                                # fallback containment for source labels without prefix
                                bare = re.sub(r"^(prov\.|kab\.|kota)\s+", "", target_norm)
                                mask = normalized.str.contains(
                                    re.escape(bare),
                                    regex=True,
                                    na=False,
                                )
                                exact = work[mask]

                            if not exact.empty:
                                values = exact["__value__"].dropna()
                                if not values.empty:
                                    found_value = float(values.iloc[0])
                                    break

                    if found_value is not None:
                        region_values[target] = found_value

                if region_values:
                    region_conf = 0.35
                else:
                    continue
            else:
                values = work["__value__"].dropna()
                if len(values) == 1:
                    region_values = {
                        instruction.target_wilayahs[0]: float(values.iloc[0])
                    }
                    region_conf = 0.20
                elif total_row_value is not None:
                    region_values = {
                        instruction.target_wilayahs[0]: total_row_value
                    }
                    region_conf = 0.25
                else:
                    continue

            confidence = min(
                1.0,
                0.30 + metric_score * 0.35 + year_conf + region_conf,
            )

            candidate = ExtractionResult(
                status="SUCCESS",
                value=(
                    region_values
                    if len(region_values) > 1
                    else next(iter(region_values.values()))
                ),
                confidence=confidence,
                source_type=source_type,
                message=f"Table {frame_index + 1}, kolom '{value_col}'",
                evidence=[
                    f"value_column={value_col}",
                    f"metric_score={metric_score:.2f}",
                    f"regions={len(region_values)}",
                ],
            )

            if best is None or candidate.confidence > best.confidence:
                best = candidate

    if best is None:
        return ExtractionResult(
            status="NEED_PARSER",
            confidence=0.0,
            source_type=source_type,
            message="Tidak menemukan tabel/kolom yang cukup yakin.",
        )

    return best
