import re
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from config import (
    DEFAULT_WILAYAH,
    INNER_CONCURRENCY,
    MAX_SCRAPE_ATTEMPTS,
    PER_DOMAIN_LIMIT,
    SCRAPE_RETRY_BACKOFF_MAX,
)
from engine.domain_limiter import ThreadDomainLimiter
from engine.formula import calculate_formula
from engine.instructions import parse_instruction
from engine.router import scrape_source_auto
from models import IndicatorJob
from sources.region_utils import normalize_region


def parse_years(value):
    text = str(value or "").strip()
    text = text.replace("–", "-").replace("—", "-")

    m = re.fullmatch(r"(\d{4})\s*-\s*(\d{4})", text)
    if m:
        start, end = map(int, m.groups())
        if start > end:
            raise ValueError("Rentang tahun tidak valid")
        return list(range(start, end + 1))

    if re.fullmatch(r"\d{4}", text):
        return [int(text)]

    if re.fullmatch(r"\d{4}(?:\s*[,;]\s*\d{4})+", text):
        return [
            int(x.strip())
            for x in re.split(r"[,;]", text)
        ]

    raise ValueError(
        "Format Tahun tidak dikenali. Gunakan 2025 atau 2019-2025."
    )


def _is_mapping(value):
    return isinstance(value, dict)


def _value_for_region(value, region):
    if not _is_mapping(value):
        return value

    if region in value:
        return value[region]

    target = normalize_region(region)

    for key, item in value.items():
        if normalize_region(key) == target:
            return item

    return None


def _round2(value):
    """
    Bulatkan nilai numerik ke 2 angka di belakang koma saat ditulis ke
    hasil akhir. Nilai None/non-angka diteruskan apa adanya.
    """
    if value is None or isinstance(value, bool):
        return value

    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return value


def _regions(default_region, *values):
    output = []
    seen = set()

    for value in values:
        if not _is_mapping(value):
            continue

        for region in value.keys():
            key = normalize_region(region)

            if key not in seen:
                seen.add(key)
                output.append(region)

    return output or [default_region]


_MULTI_SOURCE_KODES = {"3.a"}


def _is_multi_source_adapter(job, index):
    """
    Adapter tertentu (mis. TBC) memproses Sumber_1..3 dalam satu panggilan
    (Sumber_2 = pelengkap nilai provinsi). Slot sumber setelah slot 1
    tidak boleh dipanggil lagi sebagai sumber berdiri sendiri.
    """
    kode_norm = str(
        getattr(job, "kode", "") or ""
    ).strip().lower()

    return (
        kode_norm in _MULTI_SOURCE_KODES
        and index > 0
    )


def _sources_in_formula(rumus):
    """
    Ekstrak daftar sumber (1..3) yang dirujuk Rumus.
    "S1" -> {1}; "(S1/S2)*100" -> {1, 2}; "" -> {1} (default "S1");
    "100" (tanpa sumber) -> set().

    Selaras dengan calculate_formula() yang menganggap rumus kosong
    sebagai "S1": hanya sumber yang benar-benar dipakai formula yang
    wajib bernilai.
    """
    expression = str(rumus or "").strip() or "S1"

    tokens = re.findall(
        r"\bS([123])\b",
        expression.upper(),
    )

    return {
        int(token)
        for token in tokens
    }


def _build_source_context(job, description):
    """
    Router tidak boleh hanya bergantung pada Ambil_Data.

    Nama indikator + kode juga dimasukkan ke konteks supaya adapter
    seperti AKI tetap dikenali walaupun user menulis Ambil_Data singkat.
    """
    parts = [
        f"Kode {job.kode}",
        job.nama_indikator,
        description,
    ]

    return " | ".join(
        str(part).strip()
        for part in parts
        if str(part or "").strip()
    )


def _complete_existing_years(job, years, source_specs, wilayah, records):
    """
    Return (skipped_years, existing_pairs).

    skipped_years : tahun yang SELURUH wilayahnya sudah SUCCESS (tidak
                    perlu di-scrape sama sekali).
    existing_pairs: set (tahun, region) yang sudah SUCCESS — dipakai
                    untuk skip per-wilayah di tahun yang masih
                    sebagian (partial) sehingga hanya wilayah yang
                    belum ada yang diambil ulang.
    """
    if not records:
        return set(), set()

    descriptions = " | ".join(
        description
        for _, description in source_specs
        if str(description or "").strip()
    )
    context = _build_source_context(job, descriptions)
    expected_regions = parse_instruction(
        context,
        default_wilayah=wilayah,
    ).target_wilayahs or [wilayah]

    existing_keys = set()
    target_kode = str(job.kode or "").strip().casefold()

    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("Kode", "")).strip().casefold() != target_kode:
            continue
        if str(record.get("Status", "")).strip().upper() != "SUCCESS":
            continue

        try:
            tahun = int(float(record.get("Tahun", "")))
        except (TypeError, ValueError):
            continue

        region = normalize_region(record.get("Wilayah", ""))
        if region:
            existing_keys.add((tahun, region))

    skipped_years = {
        tahun
        for tahun in years
        if all(
            (tahun, normalize_region(region)) in existing_keys
            for region in expected_regions
        )
    }

    return skipped_years, existing_keys


# Status hasil scrape yang tidak perlu di-retry: sudah final.
# NEED_PARSER / LINK_TIDAK_LENGKAP dianggap transient (timeout render,
# deteksi tabel gagal, dsb.) sehingga berhak retry sampai batas percobaan.
_NON_RETRYABLE_STATUSES = {
    "SUCCESS",
    "REVIEW",
    "DATA_TIDAK_ADA",
    "SUMBER_PERLU_LOGIN",
    "SUMBER_TERBLOKIR",
}


def _is_retryable_status(status):
    return status not in _NON_RETRYABLE_STATUSES


# Penjelasan kegagalan berbahasa sederhana untuk user (bukan istilah
# teknis). Detail teknis tetap tersimpan di diagnostik job.
_FRIENDLY_REASONS = {
    "NEED_PARSER": "tidak berhasil membaca data dari halaman sumber",
    "LEGACY_FAILED": "pembaca sumber mengalami gangguan",
    "DATA_TIDAK_ADA": "sumber tidak menyediakan data untuk tahun tersebut",
    "LINK_TIDAK_LENGKAP": (
        "link tidak menampilkan datanya "
        "(bisa jadi halaman katalog)"
    ),
    "SUMBER_PERLU_LOGIN": "sumber membutuhkan login",
    "SUMBER_TERBLOKIR": (
        "situs sumber memblokir akses otomatis "
        "(proteksi keamanan)"
    ),
    "REVIEW": "data terbaca tetapi masih perlu ditinjau manual",
    "FAILED_FORMULA": (
        "nilai tidak bisa dihitung "
        "karena nilai dari sumber tidak lengkap"
    ),
    "TIMEOUT": "proses terlalu lama sehingga dihentikan",
}


def _build_failure_summary(diagnostics, used_sources, regions):
    """
    Ringkasan kegagalan per tahun, berbahasa mudah dipahami.
    Hanya dipanggil saat job tidak seluruhnya sukses.
    """
    per_year = {}

    for item in diagnostics or []:
        status = str(item.get("status") or "").strip().upper()
        if status in {"SUCCESS", "FALLBACK", "SKIPPED_EXISTING"}:
            continue

        # Kegagalan sumber yang tidak dipakai Rumus bukan kegagalan year.
        source = item.get("source")
        if source is not None and used_sources and source not in used_sources:
            continue

        reason = _FRIENDLY_REASONS.get(status, "gagal mengambil data")
        label = f"sumber {source}: {reason}" if source else reason

        # Untuk sumber terblokir, sertakan potongan error asli
        # (mis. "403 Client Error: Forbidden") agar penyebab jelas.
        if status == "SUMBER_TERBLOKIR":
            detail = str(item.get("message") or "").strip()
            if detail:
                label = f"{label} ({detail[:120]})"

        tahun = str(item.get("tahun") or "-")
        bucket = per_year.setdefault(tahun, [])
        if label not in bucket:
            bucket.append(label)

    if not per_year:
        return ""

    if len(regions) > 1:
        area = f"untuk {len(regions)} wilayah"
    elif regions:
        area = f"untuk wilayah {regions[0]}"
    else:
        area = ""

    lines = []
    for tahun in sorted(per_year):
        scope = f" ({area})" if area else ""
        lines.append(
            f"Data tahun {tahun}{scope}: "
            f"{'; '.join(per_year[tahun])}."
        )

    return "\n".join(lines)


def process_indicator_job(
    job: IndicatorJob,
    on_progress=None,
    existing_results=None,
):
    wilayah = job.wilayah or DEFAULT_WILAYAH
    years = parse_years(job.tahun)

    results = []
    diagnostics = []
    limiter = ThreadDomainLimiter(PER_DOMAIN_LIMIT)

    source_specs = [
        (job.sumber_1, job.data_diambil_1),
        (job.sumber_2, job.data_diambil_2),
        (job.sumber_3, job.data_diambil_3),
    ]
    used_sources = _sources_in_formula(job.rumus)
    skipped_years, existing_pairs = _complete_existing_years(
        job,
        years,
        source_specs,
        wilayah,
        existing_results,
    )

    for tahun in sorted(skipped_years):
        diagnostics.append({
            "tahun": tahun,
            "status": "SKIPPED_EXISTING",
            "message": "Semua wilayah yang diminta sudah berstatus SUCCESS.",
        })

    # Bangun semua kombinasi (tahun, sumber) yang perlu di-scrape.
    tasks = []
    for tahun in years:
        if tahun in skipped_years:
            continue
        for index, (url, description) in enumerate(source_specs):
            if not url:
                continue

            # Adapter yang mengambil semua sumber sekaligus (mis. TBC:
            # Sumber_2 pelengkap provinsi) sudah dipanggil di slot 1;
            # slot lain tidak perlu diproses ulang per-sumber.
            if index > 0 and _is_multi_source_adapter(job, index):
                continue

            tasks.append((tahun, index, url, description))

    total_tasks = max(1, len(tasks))
    progress_state = {
        "done": 0,
        "current": {},
    }
    progress_lock = Lock()

    def _report_progress(message, done=None, current=None):
        if on_progress is None:
            return

        with progress_lock:
            if done is not None:
                progress_state["done"] = done
            if current is not None:
                progress_state["current"] = current

            finished = progress_state["done"]
            active = progress_state.get("current") or {}

        completed_weight = finished + active.get("weight", 0)
        percent = 5 + int(90 * min(completed_weight / total_tasks, 1.0))

        try:
            on_progress({
                "progress": max(5, min(95, percent)),
                "message": message,
            })
        except Exception:
            # Progress hanya info; kegagalannya tidak boleh
            # menggagalkan job.
            pass

    def _scrape_one(task):
        tahun, index, url, description = task
        source_context = _build_source_context(job, description)
        attempts = max(1, MAX_SCRAPE_ATTEMPTS)
        last_error = None
        extracted = None

        for attempt in range(1, attempts + 1):
            label = (
                f"Percobaan {attempt}/{attempts} — "
                f"Sumber {index + 1} ({tahun})"
            )

            with progress_lock:
                done_now = progress_state["done"]

            _report_progress(
                label,
                done=done_now,
                current={
                    "weight": (attempt - 1) / attempts,
                },
            )

            try:
                with limiter(url):
                    extracted = scrape_source_auto(
                        url=url,
                        tahun=tahun,
                        description=source_context,
                        wilayah=wilayah,
                        kode=job.kode,
                        nama_indikator=job.nama_indikator,
                        sumber_2=job.sumber_2,
                        sumber_3=job.sumber_3,
                    )

                last_error = None

                if not _is_retryable_status(extracted.status):
                    break

            except Exception as error:
                last_error = error
                extracted = None

            if attempt < attempts:
                # Backoff singkat; total tetap dibatasi JOB_TIMEOUT_SECONDS.
                time.sleep(
                    min(2 * attempt, SCRAPE_RETRY_BACKOFF_MAX)
                )

        with progress_lock:
            progress_state["done"] += 1
            progress_state["current"] = {}

        if last_error is not None:
            return tahun, index, None, last_error

        return tahun, index, extracted, None

    year_values = {
        tahun: [None, None, None]
        for tahun in years
    }
    year_statuses = {
        tahun: []
        for tahun in years
    }
    year_failed = {
        tahun: False
        for tahun in years
    }

    max_workers = max(
        1,
        min(
            INNER_CONCURRENCY,
            len(tasks) or 1,
        ),
    )

    with ThreadPoolExecutor(
        max_workers=max_workers
    ) as pool:
        for (
            tahun,
            index,
            extracted,
            error,
        ) in pool.map(_scrape_one, tasks):

            if error is not None:
                diagnostics.append({
                    "tahun": tahun,
                    "source": index + 1,
                    "status": "NEED_PARSER",
                    "message": str(error).splitlines()[0][:300],
                })

                if index + 1 in used_sources:
                    year_failed[tahun] = True
                continue

            year_statuses[tahun].append(
                extracted.status
            )

            diagnostics.append({
                "tahun": tahun,
                "source": index + 1,
                "status": extracted.status,
                "confidence": extracted.confidence,
                "type": extracted.source_type,
                "message": extracted.message,
                "evidence": extracted.evidence,
            })

            if extracted.status not in {
                "SUCCESS",
                "REVIEW",
            }:
                if index + 1 in used_sources:
                    year_failed[tahun] = True
                continue

            year_values[tahun][index] = (
                extracted.value
            )

    # Beberapa job mengisi beberapa link tahunan sebagai alternatif sumber,
    # tetapi tetap memakai rumus default S1. Gunakan sumber berikutnya jika
    # S1 tidak menyediakan tahun tersebut.
    formula_text = str(job.rumus or "").strip().upper() or "S1"
    alternative_source_job = (
        formula_text == "S1"
        and any(url for url, _ in source_specs[1:])
    )
    if alternative_source_job:
        for tahun in years:
            if year_values[tahun][0] is not None:
                continue

            for index in (1, 2):
                fallback_value = year_values[tahun][index]
                if fallback_value is None:
                    continue

                year_values[tahun][0] = fallback_value
                year_failed[tahun] = False
                for diagnostic in diagnostics:
                    if (
                        diagnostic.get("tahun") == tahun
                        and diagnostic.get("source") == 1
                        and diagnostic.get("status") in {
                            "NEED_PARSER",
                            "DATA_TIDAK_ADA",
                            "LINK_TIDAK_LENGKAP",
                            "SUMBER_TERBLOKIR",
                        }
                    ):
                        diagnostic["status"] = "FALLBACK"
                        diagnostic["message"] = (
                            "Sumber alternatif dipakai karena Sumber_1 "
                            f"tidak menyediakan tahun {tahun}."
                        )
                break

    # (tahun, wilayah) yang sudah SUCCESS — tidak boleh ditulis ulang.
    # Dipakai untuk tahun partial: hanya wilayah yang belum ada yang
    # menghasilkan baris hasil baru.
    skipped_pairs_by_year = {}

    def _skip_existing(tahun, region):
        if (tahun, normalize_region(region)) in existing_pairs:
            skipped_pairs_by_year.setdefault(tahun, []).append(region)
            return True
        return False

    for tahun in years:
        if tahun in skipped_years:
            continue

        if any(
            status == "REVIEW"
            for status in year_statuses[tahun]
        ):
            for region in _regions(wilayah, None, None, None):
                if _skip_existing(tahun, region):
                    continue

                results.append({
                    "kode": job.kode,
                    "nama_indikator": job.nama_indikator,
                    "wilayah": region,
                    "tahun": tahun,
                    "nilai_s1": None,
                    "nilai_s2": None,
                    "nilai_s3": None,
                    "hasil": "-",
                    "status": "REVIEW",
                    "error": "",
                })
            continue

        # Formula hanya memakai sumber yang dirujuk Rumus. Sumber lain
        # yang gagal tidak boleh membatalkan tahun ini (mis. Sumber_2
        # hanya pelengkap provinsi untuk indikator tertentu).
        source_failed = year_failed[tahun]

        for slot in used_sources:
            if year_values[tahun][slot - 1] is None:
                source_failed = True
                break

        if source_failed:
            for region in _regions(wilayah, None, None, None):
                if _skip_existing(tahun, region):
                    continue

                results.append({
                    "kode": job.kode,
                    "nama_indikator": job.nama_indikator,
                    "wilayah": region,
                    "tahun": tahun,
                    "nilai_s1": None,
                    "nilai_s2": None,
                    "nilai_s3": None,
                    "hasil": "-",
                    "status": "DATA_TIDAK_ADA",
                    "error": "",
                })
            continue

        s1, s2, s3 = year_values[tahun]

        region_targets = _regions(
            wilayah,
            s1,
            s2,
            s3,
        )

        for region in region_targets:
            if _skip_existing(tahun, region):
                continue

            r1 = _value_for_region(
                s1,
                region,
            )
            r2 = _value_for_region(
                s2,
                region,
            )
            r3 = _value_for_region(
                s3,
                region,
            )

            try:
                hasil = calculate_formula(
                    job.rumus,
                    {
                        "S1": r1,
                        "S2": r2,
                        "S3": r3,
                    },
                )

            except Exception as error:
                diagnostics.append({
                    "tahun": tahun,
                    "wilayah": region,
                    "status": "FAILED_FORMULA",
                    "message": str(error),
                })
                continue

            results.append({
                "kode": job.kode,
                "nama_indikator": job.nama_indikator,
                "wilayah": region,
                "tahun": tahun,
                "nilai_s1": _round2(r1),
                "nilai_s2": _round2(r2),
                "nilai_s3": _round2(r3),
                "hasil": _round2(hasil),
                "status": "SUCCESS",
                "error": "",
            })

    # Diagnostic per tahun yang di-scrape sebagian: wilayah yang sudah
    # SUCCESS dilewati, hanya yang belum ada yang diambil ulang.
    for tahun in sorted(skipped_pairs_by_year):
        regions = skipped_pairs_by_year[tahun]
        diagnostics.append({
            "tahun": tahun,
            "status": "SKIPPED_EXISTING",
            "message": (
                f"{len(regions)} wilayah sudah SUCCESS; "
                "hanya wilayah yang belum ada data yang diambil ulang."
            ),
            "wilayah": regions,
        })

    statuses = {
        d.get("status")
        for d in diagnostics
        if (
            d.get("source") is None
            or d.get("source") in used_sources
        )
    }

    has_success_results = any(
        result.get("status") == "SUCCESS"
        for result in results
    )
    has_review_results = any(
        result.get("status") == "REVIEW"
        for result in results
    )

    if years and all(tahun in skipped_years for tahun in years):
        final_status = "SUCCESS"

    elif (
        has_success_results
        and "REVIEW" not in statuses
    ):
        final_status = "SUCCESS"

    elif has_success_results:
        final_status = "PARTIAL"

    elif has_review_results or "REVIEW" in statuses:
        final_status = "REVIEW"

    elif "SUMBER_PERLU_LOGIN" in statuses:
        final_status = "SUMBER_PERLU_LOGIN"

    elif "SUMBER_TERBLOKIR" in statuses:
        final_status = "SUMBER_TERBLOKIR"

    elif "LINK_TIDAK_LENGKAP" in statuses:
        final_status = "LINK_TIDAK_LENGKAP"

    elif "DATA_TIDAK_ADA" in statuses:
        final_status = "DATA_TIDAK_ADA"

    else:
        final_status = "NEED_PARSER"

    failure_summary = ""
    if final_status != "SUCCESS":
        descriptions = " | ".join(
            description
            for _, description in source_specs
            if str(description or "").strip()
        )
        expected_regions = parse_instruction(
            _build_source_context(job, descriptions),
            default_wilayah=wilayah,
        ).target_wilayahs or [wilayah]
        failure_summary = _build_failure_summary(
            diagnostics,
            used_sources,
            expected_regions,
        )

    return {
        "status": final_status,
        "results": results,
        "diagnostics": diagnostics,
        "failure_summary": failure_summary,
    }
