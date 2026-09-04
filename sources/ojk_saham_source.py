"""
Adapter NILAI TRANSAKSI SAHAM per kapita per Provinsi (Auto project).

Menggunakan helper Selenium OJK dari project legacy (dataset 277),
tetapi mengekstrak SEMUA baris wilayah (provinsi + kabupaten/kota),
bukan hanya "Kalimantan Selatan".

Return berupa dict {wilayah: nilai} sesuai konvensi run_indicator,
sehingga seluruh wilayah yang datanya tersedia di portal OJK ikut
masuk ke hasil dashboard.
"""

import importlib
import os
import re
import sys

from config import LEGACY_INDICATORS_PATH


KALSEL_PROVINSI = [
    "Prov. Kalimantan Selatan",
]

KALSEL_KAB_KOTA = [
    "Kab. Tanah Laut",
    "Kab. Kotabaru",
    "Kab. Banjar",
    "Kab. Barito Kuala",
    "Kab. Tapin",
    "Kab. Hulu Sungai Selatan",
    "Kab. Hulu Sungai Tengah",
    "Kab. Hulu Sungai Utara",
    "Kab. Tabalong",
    "Kab. Tanah Bumbu",
    "Kab. Balangan",
    "Kota Banjarmasin",
    "Kota Banjarbaru",
]

KALSEL_ALL = KALSEL_PROVINSI + KALSEL_KAB_KOTA


class OjkSahamUnavailableError(RuntimeError):
    """Data transaksi saham tidak tersedia untuk tahun yang diminta."""


def _load_ojk():
    path = os.path.abspath(LEGACY_INDICATORS_PATH)
    inserted = False

    if path and path not in sys.path:
        sys.path.insert(0, path)
        inserted = True

    try:
        return importlib.import_module("ojk")
    finally:
        if inserted and path in sys.path:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


def _normalize_region(value):
    text = " ".join(str(value or "").strip().lower().split())
    return text


def _region_variants(name):
    """
    Bangun daftar variasi nama wilayah untuk dicocokkan dengan
    label row OJK (provinsi/kabupaten/kota).

    Contoh "Kab. Hulu Sungai Selatan":
        hulu sungai selatan
        kab. hulu sungai selatan
        kabupaten hulu sungai selatan
        kab hulu sungai selatan
    """
    norm = _normalize_region(name)

    variants = set()

    # Nama tanpa awalan (paling mungkin dipakai DATI2).
    bare = norm
    for prefix in (
        "kabupaten ",
        "kab. ",
        "kab ",
        "kota ",
        "provinsi ",
        "prov. ",
        "prov ",
    ):
        if bare.startswith(prefix):
            bare = bare[len(prefix):]
            break

    if bare:
        variants.add(bare)

    variants.add(norm)

    if norm.startswith("kab. "):
        rest = norm[len("kab. "):]
        variants.add("kabupaten " + rest)
        variants.add("kab " + rest)

    if norm.startswith("kota "):
        rest = norm[len("kota "):]
        variants.add("kota " + rest)

    if norm.startswith("prov. "):
        rest = norm[len("prov. "):]
        variants.add("provinsi " + rest)
        variants.add("prov " + rest)

    return {
        variant
        for variant in variants
        if variant
    }


def _resolve_targets(description, default_wilayah):
    """
    Tentukan daftar wilayah yang diminta dari teks Ambil_Data.

    Mengikuti pola AKI: deteksi kata kunci kab/kota & provinsi.
    """
    lower = str(description or "").lower()

    wants_kab_kota = any(
        marker in lower
        for marker in (
            "kab/kota",
            "kabupaten/kota",
            "kabupaten dan kota",
            "seluruh kab",
            "semua kab",
            "kab kota",
        )
    )

    wants_provinsi = any(
        marker in lower
        for marker in (
            "prov. kalsel",
            "prov kalsel",
            "provinsi kalsel",
            "provinsi kalimantan selatan",
            "prov. kalimantan selatan",
            "dan prov",
            "provinsi +",
        )
    )

    if wants_kab_kota and wants_provinsi:
        return KALSEL_ALL

    if wants_kab_kota:
        return KALSEL_KAB_KOTA

    return [
        str(default_wilayah or "").strip()
        or KALSEL_PROVINSI[0]
    ]


def _canonical_wilayah(row_label):
    """
    Cocokkan label row dari pivot OJK dengan nama wilayah kanonik Kalsel.

    Menggunakan word boundary regex untuk menghindari false match
    (mis. "banjar" vs "banjarbaru").
    """
    label = _normalize_region(row_label)

    if not label:
        return None

    # Urutkan wilayah dari nama terpanjang agar spesifik lebih dulu.
    for name in sorted(
        KALSEL_ALL,
        key=lambda n: -len(n),
    ):
        for variant in _region_variants(name):
            pattern = (
                r"(?<![a-z0-9])"
                + re.escape(variant)
                + r"(?![a-z0-9])"
            )

            if re.search(pattern, label):
                return name

    return None


def _pair_from_cell(cell, buy_measure_index, sell_measure_index, ojk):
    if not isinstance(cell, (list, tuple)):
        return None

    if len(cell) <= sell_measure_index:
        return None

    buy = ojk.parse_ojk_number(cell[buy_measure_index])
    sell = ojk.parse_ojk_number(cell[sell_measure_index])

    if buy is None or sell is None:
        return None

    return (float(buy), float(sell))


def _extract_row_annual(matrix_row, grand_col, buy_measure_index,
                        sell_measure_index, ojk):
    if not isinstance(matrix_row, (list, tuple)):
        return None

    # Grand total column jika valid.
    if grand_col is not None:
        try:
            grand_col = int(grand_col)
            if 0 <= grand_col < len(matrix_row):
                pair = _pair_from_cell(
                    matrix_row[grand_col],
                    buy_measure_index,
                    sell_measure_index,
                    ojk,
                )
                if pair is not None:
                    return pair
        except (TypeError, ValueError):
            pass

    pairs = []
    for cell in matrix_row:
        pair = _pair_from_cell(
            cell,
            buy_measure_index,
            sell_measure_index,
            ojk,
        )
        if pair is not None:
            pairs.append(pair)

    if len(pairs) == 1:
        return pairs[0]

    if len(pairs) == 12:
        return (
            sum(item[0] for item in pairs),
            sum(item[1] for item in pairs),
        )

    return None


def scrape_ojk_saham_annual(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Ambil Nilai Transaksi Saham tahunan (Pembelian + Penjualan)
    untuk SEMUA wilayah yang tersedia di portal OJK dataset 277.

    Return dict {wilayah: nilai}.
    """
    tahun = int(tahun)
    ojk = _load_ojk()

    dataset_id = ojk.ojk_dataset_id_from_url(url) or "unknown"

    driver = ojk.create_chrome_driver()

    try:
        driver.set_page_load_timeout(90)
        driver.get(url)

        from selenium.webdriver.support.ui import WebDriverWait

        WebDriverWait(driver, 45).until(
            lambda d: d.execute_script(
                "return document.readyState"
            ) == "complete"
        )

        import time
        time.sleep(8)

        ojk.ojk_wait_for_pivotgrid(driver, timeout=45)

        context = ojk.ojk_stock_prepare_dataset277(
            driver,
            tahun=tahun,
        )

        month_items = context.get("months", []) or []

        if len(month_items) != 12:
            raise OjkSahamUnavailableError(
                f"Dataset 277 tahun {tahun} "
                f"tidak memiliki 12 key bulan."
            )

        # Deteksi satuan (ke miliar) dari baris provinsi.
        factor, unit_label = ojk.ojk_stock_detect_unit_to_miliar(driver)

        ojk.ojk_stock_select_all_months(
            driver,
            month_items=month_items,
        )

        import time
        time.sleep(4)

        pivot_data = ojk.ojk_get_loaded_pivot_data(driver)

        rows = pivot_data.get("rows", []) or []
        values = pivot_data.get("values", []) or []
        grand_col = pivot_data.get("grandTotalColumnIndex")

        buy_measure_index = 2
        sell_measure_index = 5

        # Infer unit dari baris provinsi bila label satuan tidak tampil.
        if factor is None:
            for row in rows:
                label = (
                    row.get("path")
                    or row.get("text")
                    or row.get("value")
                    or ""
                )
                canon = _canonical_wilayah(label)

                if canon == KALSEL_PROVINSI[0]:
                    try:
                        row_index = int(row.get("index"))
                        matrix_row = values[row_index]
                        pair = _extract_row_annual(
                            matrix_row,
                            grand_col,
                            buy_measure_index,
                            sell_measure_index,
                            ojk,
                        )
                        if pair is not None:
                            factor, unit_label = (
                                ojk.ojk_stock_infer_unit_from_raw(
                                    pair[0],
                                    pair[1],
                                )
                            )
                            break
                    except (TypeError, ValueError, IndexError):
                        continue

        results = {}

        for row in rows:
            label = (
                row.get("path")
                or row.get("text")
                or row.get("value")
                or ""
            )

            canon = _canonical_wilayah(label)

            if canon is None:
                continue

            try:
                row_index = int(row.get("index"))
                matrix_row = values[row_index]
            except (TypeError, ValueError, IndexError):
                continue

            pair = _extract_row_annual(
                matrix_row,
                grand_col,
                buy_measure_index,
                sell_measure_index,
                ojk,
            )

            if pair is None:
                continue

            annual = (
                float(pair[0])
                + float(pair[1])
            )

            if factor is not None:
                annual = annual * factor

            if annual < 0:
                continue

            results[canon] = annual

        targets = _resolve_targets(description, wilayah)
        filtered = {
            target: results[target]
            for target in targets
            if target in results
        }

        if not filtered:
            if not results:
                raise OjkSahamUnavailableError(
                    "Dataset 277 tidak mengembalikan data "
                    "untuk wilayah Kalimantan Selatan."
                )

            print(
                f"    [OJK-SAHAM] Tahun {tahun}: wilayah yang diminta "
                "tidak tersedia di sumber."
            )
            return {}

        print(
            f"    [OJK-SAHAM] Berhasil {len(filtered)} wilayah "
            f"untuk tahun {tahun}."
        )

        if len(filtered) == 1:
            return next(iter(filtered.values()))

        return filtered

    finally:
        try:
            driver.quit()
        except Exception:
            pass
