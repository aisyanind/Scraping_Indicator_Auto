import json
import re

from sources.bps_utils import (
    detect_rentang,
    download_html,
    extract_metric_terms,
    with_year_param,
    METRIC_SYNONYMS,
)

_CACHE = {}


def _extract_from_rsc_payload(html):
    """
    Extract data dari BPS React Server Components (RSC) payload.

    BPS SPA mengirim data via self.__next_f.push([1,"...json..."]).
    Cari object dengan key "datacontent" yang berisi data tabel.
    """
    for match in re.finditer(
        r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)',
        html,
    ):
        try:
            raw = json.loads('"' + match.group(1) + '"')
        except (json.JSONDecodeError, ValueError):
            continue

        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            colon = line.find(":")
            if colon < 1:
                continue
            payload = line[colon + 1:]
            if not payload or payload[0] not in "{[":
                continue
            try:
                parsed = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue

            data_obj = parsed
            if isinstance(parsed, list) and len(parsed) >= 4 and isinstance(parsed[3], dict):
                data_obj = parsed[3]

            if isinstance(data_obj, dict) and "datacontent" in data_obj:
                return data_obj

    return None


def _pick_best_from_rsc(data_obj, metric_terms, target_wilayah, tahun):
    """Pilih nilai terbaik dari RSC datacontent berdasarkan metric + tahun."""
    datacontent = data_obj.get("datacontent", {})
    vervar_list = data_obj.get("vervar", [])
    tahun_list = data_obj.get("tahun", [])
    turvar_list = data_obj.get("turvar", [])
    var_list = data_obj.get("var", [])

    if not datacontent or not vervar_list:
        return None

    tahun_str = str(tahun)
    tahun_ids = set()
    for t in tahun_list:
        if isinstance(t, dict):
            label = str(t.get("label", ""))
            if tahun_str in label:
                tahun_ids.add(t.get("val"))
        elif str(t) == tahun_str:
            tahun_ids.add(t)

    target_lower = (target_wilayah or "").lower()

    candidates = []
    for key, value in datacontent.items():
        if not isinstance(value, (int, float)) or value <= 0:
            continue
        key_str = str(key)
        parts = key_str.split()
        if len(parts) < 2:
            continue

        score = 0.0

        if target_lower:
            vervar_id = parts[0]
            for v in vervar_list:
                if isinstance(v, dict) and str(v.get("val")) == vervar_id:
                    label = str(v.get("label", "")).lower()
                    if target_lower in label or label in target_lower:
                        score += 3.0
                    elif any(w in label for w in target_lower.split() if len(w) > 3):
                        score += 1.5
                    break

        for term in metric_terms:
            if term in key_str:
                score += 1.0
            else:
                synonyms = METRIC_SYNONYMS.get(term, [])
                if any(syn in key_str for syn in synonyms):
                    score += 0.7

        if tahun_ids and len(parts) >= 3:
            for pid in parts[1:]:
                if pid.isdigit() and int(pid) in tahun_ids:
                    score += 2.0
                    break

        candidates.append((value, score, key_str))

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c[1])
    return best[0]


def _extract_from_next_data(data, metric_terms, target_wilayah, tahun):
    """Recursive search di __NEXT_DATA__ untuk menemukan tabel data."""
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
                result = _pick_best_from_table(
                    val, metric_terms, target_wilayah, tahun
                )
                if result is not None:
                    return result

        for key, val in data.items():
            if key.startswith("__"):
                continue
            result = _extract_from_next_data(val, metric_terms, target_wilayah, tahun)
            if result is not None:
                return result

    elif isinstance(data, list):
        for item in data:
            result = _extract_from_next_data(item, metric_terms, target_wilayah, tahun)
            if result is not None:
                return result

    return None


def _pick_best_from_table(rows, metric_terms, target_wilayah, tahun):
    """Pilih baris terbaik dari tabel berdasarkan metric_terms dan wilayah."""
    if not rows or not isinstance(rows, list):
        return None

    candidates = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        for key, val in row.items():
            if not isinstance(val, (int, float)):
                continue
            if val <= 0:
                continue

            key_lower = key.lower()

            wilayah_score = 0
            if target_wilayah:
                target_lower = target_wilayah.lower()
                if target_lower in key_lower or key_lower in target_lower:
                    wilayah_score = 2.0
                elif any(part in key_lower for part in target_lower.split() if len(part) > 3):
                    wilayah_score = 1.0

            metric_score = 0
            for term in metric_terms:
                if term in key_lower:
                    metric_score += 1.0
                else:
                    synonyms = METRIC_SYNONYMS.get(term, [])
                    if any(syn in key_lower for syn in synonyms):
                        metric_score += 0.7

            tahun_score = 0
            if str(tahun) in str(row.values()):
                tahun_score = 1.0

            total_score = wilayah_score + metric_score + tahun_score

            candidates.append((val, total_score, key))

    if not candidates:
        return None

    best = max(candidates, key=lambda c: c[1])
    return best[0]


def _extract_value_raw_with_context(html, metric_terms):
    """Extract value_raw dari HTML dengan context scoring."""
    from sources.bps_utils import parse_number_id

    candidates = []

    for match in re.finditer(
        r'value_raw\D{0,30}(\d[\d., ]*\d|\d)',
        html,
    ):
        raw = match.group(1)
        text = raw.strip().replace(" ", "")

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
            nilai = float(text)
        except ValueError:
            continue

        start = max(0, match.start() - 300)
        end = min(len(html), match.end() + 300)
        context = html[start:end].lower()

        context_score = 0
        for term in metric_terms:
            if term in context:
                context_score += 1.0
            else:
                synonyms = METRIC_SYNONYMS.get(term, [])
                if any(syn in context for syn in synonyms):
                    context_score += 0.7

        if metric_terms:
            context_score /= max(len(metric_terms), 1)

        candidates.append((nilai, context_score))

    if not candidates:
        return None

    best_by_context = max(candidates, key=lambda c: c[1])
    if best_by_context[1] >= 0.5 and best_by_context[0] > 0:
        return best_by_context[0]

    all_values = [c[0] for c in candidates if c[0] > 0]
    if all_values:
        return max(all_values)

    return None


def try_bps_table(url, tahun, description=None, wilayah=None):
    """
    Template generic untuk tabel BPS.

    Return ExtractionResult atau None.
    """
    tahun = int(tahun)
    lower_url = str(url or "").lower()

    if "bps.go.id" not in lower_url:
        return None

    if "/statistics-table/" not in lower_url:
        return None

    cache_key = (str(url or "").strip(), tahun, str(description or ""))
    if cache_key in _CACHE:
        from models import ExtractionResult
        return ExtractionResult(
            status="SUCCESS",
            value={"__main__": _CACHE[cache_key]},
            confidence=0.95,
            source_type="bps_table_cached",
            message="Data dari cache.",
            evidence=["cached=true"],
        )

    metric_terms = extract_metric_terms(description)
    rentang = detect_rentang(metric_terms)

    print(
        f"    [BPS-TABLE] Template: metric={metric_terms}, "
        f"rentang={rentang}, tahun={tahun}"
    )

    target_url = with_year_param(url, tahun)

    try:
        html = download_html(target_url, tag="BPS-TABLE")
    except Exception as error:
        from models import ExtractionResult
        return ExtractionResult(
            status="NEED_PARSER",
            value=None,
            confidence=0.0,
            source_type="bps_table",
            message=f"Gagal download: {str(error)[:200]}",
            evidence=[],
        )

    # Method 1: RSC payload (React Server Components) — BPS terbaru.
    rsc_data = _extract_from_rsc_payload(html)
    if rsc_data is not None:
        nilai = _pick_best_from_rsc(rsc_data, metric_terms, wilayah, tahun)
        if nilai is not None and rentang[0] <= nilai <= rentang[1]:
            _CACHE[cache_key] = nilai
            from models import ExtractionResult
            return ExtractionResult(
                status="SUCCESS",
                value={"__main__": nilai},
                confidence=0.85,
                source_type="bps_table_rsc",
                message=f"Data dari RSC payload: {nilai}",
                evidence=[f"rsc_value={nilai}"],
            )

    # Method 2: __NEXT_DATA__ (Next.js Pages Router — BPS lama).
    next_match = re.search(
        r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if next_match:
        try:
            data = json.loads(next_match.group(1))
            nilai = _extract_from_next_data(data, metric_terms, wilayah, tahun)
            if nilai is not None and rentang[0] <= nilai <= rentang[1]:
                _CACHE[cache_key] = nilai
                from models import ExtractionResult
                return ExtractionResult(
                    status="SUCCESS",
                    value={"__main__": nilai},
                    confidence=0.85,
                    source_type="bps_table_nextjs",
                    message=f"Data dari __NEXT_DATA__: {nilai}",
                    evidence=[f"nextjs_value={nilai}"],
                )
        except (json.JSONDecodeError, ValueError):
            pass

    nilai = _extract_value_raw_with_context(html, metric_terms)
    if nilai is not None and rentang[0] <= nilai <= rentang[1]:
        _CACHE[cache_key] = nilai
        from models import ExtractionResult
        return ExtractionResult(
            status="SUCCESS",
            value={"__main__": nilai},
            confidence=0.75,
            source_type="bps_table_value_raw",
            message=f"Data dari value_raw: {nilai}",
            evidence=[f"value_raw={nilai}"],
        )

    from models import ExtractionResult
    return ExtractionResult(
        status="NEED_PARSER",
        value=None,
        confidence=0.0,
        source_type="bps_table",
        message=f"Tidak ditemukan data BPS yang cocok (metric={metric_terms})",
        evidence=[],
    )


__all__ = ["try_bps_table"]
