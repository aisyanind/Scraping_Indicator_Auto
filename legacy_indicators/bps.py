from utils import *
import os
import re
import tempfile
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


# ============================================================
# EXTRACT PDRB
# ============================================================

def extract_pdrb_value(
    text,
    tahun
):

    if not text:
        return None

    lines = [
        re.sub(
            r"\s+",
            " ",
            line
        ).strip()

        for line in (
            text
            .replace(
                "\xa0",
                " "
            )
            .splitlines()
        )

        if line.strip()
    ]

    number_pattern = re.compile(
        r"(?<!\d)"
        r"\d{1,3}"
        r"(?:\.\d{3})+"
        r"(?:,\d+)?"
        r"(?!\d)"
    )

    for index, line in enumerate(
        lines
    ):

        lower = (
            line.lower()
        )

        # Baris TOTAL PDRB
        if (
            "produk domestik regional bruto"
            not in lower
            and
            "gross regional domestic product"
            not in lower
        ):
            continue

        # Jangan ambil tabel turunan
        if (
            "per kapita"
            in lower
            or
            "distribusi"
            in lower
            or
            "pertumbuhan"
            in lower
        ):
            continue

        print(
            f"    [BPS] Label total   = "
            f"{line}"
        )

        # Nilai biasanya ada pada baris yang sama
        # atau beberapa line setelah label.
        end_index = min(
            len(lines),
            index + 6
        )

        for check_index in range(
            index,
            end_index
        ):

            check_line = (
                lines[
                    check_index
                ]
            )

            numbers = (
                number_pattern.findall(
                    check_line
                )
            )

            for raw in numbers:

                value = (
                    parse_indonesian_number(
                        raw
                    )
                )

                if (
                    value is not None
                    and
                    50_000
                    <= value
                    <= 1_000_000
                ):

                    print(
                        f"    [BPS] Raw PDRB      = "
                        f"{raw}"
                    )

                    return float(
                        value
                    )

    return None


# ============================================================
# BPS SCRAPER
# ============================================================

def scrape_bps_pdrb(
    url,
    tahun,
    description
):

    cache_key = (
        "bps_pdrb",
        url,
        int(tahun)
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[
            cache_key
        ]

    target_url = (
        set_year_parameter(
            url,
            tahun
        )
    )

    print(
        f"    [BPS] Membuka "
        f"PDRB tahun {tahun}..."
    )

    max_attempts = 3

    for attempt in range(1, max_attempts + 1):

        driver = create_chrome_driver()

        try:

            driver.set_page_load_timeout(60)

            driver.get(target_url)

            WebDriverWait(
                driver,
                40,
            ).until(
                lambda d:
                d.execute_script(
                    "return document.readyState"
                )
                ==
                "complete"
            )

            # Tunggu tabel awal dimuat.
            # Diperpanjang karena loading BPS kadang lambat.
            time.sleep(8)

            last_height = 0
            collected_text = ""
            found = False

            for step in range(25):

                current_height = (
                    driver.execute_script(
                        "return document.body.scrollHeight;"
                    )
                )

                scroll_position = min(
                    (step + 1) * 700,
                    current_height
                )

                driver.execute_script(
                    "window.scrollTo("
                    "0, arguments[0]"
                    ");",
                    scroll_position,
                )

                time.sleep(1.5)

                page_text = (
                    collect_page_text(
                        driver
                    )
                )

                if page_text:
                    collected_text += (
                        "\n"
                        +
                        page_text
                    )

                lower_text = (
                    collected_text
                    .lower()
                )

                if (
                    "produk domestik regional bruto"
                    in lower_text
                    or
                    "gross regional domestic product"
                    in lower_text
                ):
                    print(
                        "    [BPS] Baris PDRB ditemukan "
                        f"pada percobaan {attempt}, "
                        f"scroll step {step + 1}"
                    )
                    found = True
                    break

                current_scroll = (
                    driver.execute_script(
                        "return "
                        "window.pageYOffset "
                        "+ window.innerHeight;"
                    )
                )

                if (
                    current_scroll
                    >=
                    current_height - 50
                ):
                    break

                last_height = current_height

            # Paksa ke paling bawah sekali lagi + tunggu ekstra
            # kalau belum ketemu, siapa tahu render telat.
            if not found:

                print(
                    f"    [BPS] Belum ketemu di percobaan "
                    f"{attempt}, menunggu ekstra..."
                )

                time.sleep(5)

                driver.execute_script(
                    "window.scrollTo("
                    "0, document.body.scrollHeight"
                    ");"
                )

                time.sleep(4)

                bottom_text = (
                    collect_page_text(
                        driver
                    )
                )

                if bottom_text:
                    collected_text += (
                        "\n"
                        +
                        bottom_text
                    )

                lower_text = (
                    collected_text
                    .lower()
                )

                if (
                    "produk domestik regional bruto"
                    in lower_text
                    or
                    "gross regional domestic product"
                    in lower_text
                ):
                    found = True

            if found:

                print(
                    "    [BPS] Label PDRB    = ditemukan"
                )

                pdrb = (
                    extract_pdrb_value(
                        collected_text,
                        tahun
                    )
                )

                if pdrb is not None:

                    print(
                        f"    [BPS] PDRB {tahun} = "
                        f"{format_id(pdrb)} "
                        "miliar Rp"
                    )

                    SCRAPE_CACHE[
                        cache_key
                    ] = float(pdrb)

                    return float(pdrb)

                print(
                    "    [BPS] Label ditemukan tetapi "
                    "nilai gagal di-parse."
                )

            else:

                print(
                    "    [BPS] Label PDRB    = TIDAK ditemukan "
                    f"(percobaan {attempt}/{max_attempts})"
                )

            # Simpan debug hanya di percobaan terakhir.
            if attempt == max_attempts:

                debug_path = os.path.join(
                    tempfile.gettempdir(),
                    "bps_pdrb_debug.txt",
                )

                try:

                    with open(
                        debug_path,
                        "w",
                        encoding="utf-8"
                    ) as file:

                        file.write(
                            collected_text
                        )

                    print(
                        f"    [BPS] Debug text    = "
                        f"{debug_path}"
                    )

                except Exception:

                    pass

        finally:

            try:

                driver.quit()

            except Exception:

                pass

        # Jeda sebelum retry, kasih waktu server "reset".
        if attempt < max_attempts:
            time.sleep(6)

    raise RuntimeError(
        "Nilai PDRB tidak ditemukan setelah "
        f"{max_attempts} percobaan (reload penuh + scroll)."
    )


# ============================================================
# BPS: JUMLAH PENDUDUK KALIMANTAN SELATAN
# ============================================================

def extract_bps_population_value(
    text,
    tahun
):
    """
    Ekstrak jumlah penduduk Provinsi Kalimantan Selatan
    dari tabel BPS.

    Return:
        (jumlah_penduduk_dalam_jiwa, raw_value, unit_source)

    Format yang didukung:
        4.221,93  -> ribu jiwa -> 4.221.930 jiwa
        4.221.930 -> jiwa penuh
        4221930   -> jiwa penuh
    """

    if not text:
        return None

    lines = [
        re.sub(
            r"\s+",
            " ",
            line.replace("\xa0", " ")
        ).strip()
        for line in text.splitlines()
        if line.strip()
    ]

    def is_kalsel_label(value):
        normalized = str(value or "").strip().lower()
        normalized = re.sub(
            r"^\s*\d+\s*[.)-]?\s*",
            "",
            normalized,
        )
        normalized = normalized.replace(
            "provinsi ",
            ""
        ).strip()

        return (
            normalized == "kalimantan selatan"
            or
            normalized.startswith("kalimantan selatan ")
        )

    number_pattern = re.compile(
        r"(?<!\d)"
        r"(?:"
        r"\d{1,3}(?:\.\d{3})+(?:,\d+)?"
        r"|"
        r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
        r"|"
        r"\d{4,9}(?:[.,]\d+)?"
        r")"
        r"(?!\d)"
    )

    def parse_candidate(raw):
        value = parse_indonesian_number(raw)

        if value is None:
            return None

        value = float(value)

        if 3_000 <= value <= 6_000:
            return (
                value * 1000,
                "ribu jiwa",
            )

        if 3_000_000 <= value <= 6_000_000:
            return (
                value,
                "jiwa",
            )

        return None

    # Prioritas utama: angka yang muncul dekat label Kalsel.
    for index, line in enumerate(lines):
        if not is_kalsel_label(line):
            continue

        chunk = " ".join(
            lines[
                index:
                min(len(lines), index + 12)
            ]
        )

        for raw in number_pattern.findall(chunk):
            parsed = parse_candidate(raw)

            if parsed is None:
                continue

            population, unit_source = parsed

            return (
                float(population),
                raw,
                unit_source,
            )

    # Fallback: satu baris memuat nama wilayah dan angka.
    for line in lines:
        if "kalimantan selatan" not in line.lower():
            continue

        for raw in number_pattern.findall(line):
            parsed = parse_candidate(raw)

            if parsed is None:
                continue

            population, unit_source = parsed

            return (
                float(population),
                raw,
                unit_source,
            )

    return None


def scrape_bps_population(
    url,
    tahun,
    description
):
    """
    Ambil jumlah penduduk Provinsi Kalimantan Selatan
    dari tabel BPS.

    Output selalu dalam jiwa.

    PENTING: pakai retry (mirip scrape_bps_pdrb) karena render
    tabel BPS kadang lambat/tidak konsisten -- gagal sekali bukan
    berarti datanya memang tidak ada. Lihat kasus 2023 yang gagal
    padahal 2024/2025 sukses dengan URL & kode yang sama persis.
    """

    tahun = int(tahun)

    cache_key = (
        "bps_population_kalsel",
        url,
        tahun,
    )

    if cache_key in SCRAPE_CACHE:
        return SCRAPE_CACHE[cache_key]

    target_url = set_year_parameter(
        url,
        tahun
    )

    print(
        f"    [BPS-PENDUDUK] Membuka jumlah penduduk "
        f"tahun {tahun}..."
    )

    max_attempts = 3
    last_page_text = ""

    for attempt in range(1, max_attempts + 1):

        driver = create_chrome_driver()

        try:
            driver.set_page_load_timeout(60)

            driver.get(target_url)

            WebDriverWait(
                driver,
                40,
            ).until(
                lambda d:
                d.execute_script(
                    "return document.readyState"
                )
                ==
                "complete"
            )

            # Tunggu lebih lama tiap percobaan berikutnya,
            # siapa tahu render-nya cuma lambat.
            time.sleep(7 + (attempt - 1) * 5)

            driver.execute_script(
                "window.scrollTo("
                "0, "
                "document.body.scrollHeight / 2"
                ");"
            )

            time.sleep(3)

            page_text = collect_page_text(driver)
            last_page_text = page_text or last_page_text

            extracted = extract_bps_population_value(
                page_text,
                tahun,
            )

            if extracted is None:
                print(
                    f"    [BPS-PENDUDUK] Belum ketemu di percobaan "
                    f"{attempt}/{max_attempts}, coba lagi..."
                )
                continue

            population, raw_value, unit_source = extracted

            if not (
                3_000_000
                <= population
                <= 6_000_000
            ):
                print(
                    f"    [BPS-PENDUDUK] Nilai di luar rentang wajar "
                    f"pada percobaan {attempt}: {population}, coba lagi..."
                )
                continue

            print(
                "    [BPS-PENDUDUK] Raw BPS = "
                f"{raw_value} ({unit_source})"
            )

            print(
                f"    [BPS-PENDUDUK] Penduduk {tahun} = "
                f"{format_id(population, 0)} jiwa"
            )

            SCRAPE_CACHE[cache_key] = float(population)

            return float(population)

        finally:
            try:
                driver.quit()
            except Exception:
                pass

        if attempt < max_attempts:
            time.sleep(5)

    # Simpan debug supaya bisa dicek kalau tetap gagal setelah retry.
    debug_path = os.path.join(
        tempfile.gettempdir(),
        "bps_population_debug.txt",
    )

    try:
        with open(debug_path, "w", encoding="utf-8") as file:
            file.write(last_page_text)
        print(f"    [BPS-PENDUDUK] Debug text = {debug_path}")
    except Exception:
        pass

    raise RuntimeError(
        f"Jumlah penduduk Provinsi Kalimantan Selatan tahun {tahun} "
        f"tidak ditemukan setelah {max_attempts} percobaan. "
        "Pastikan Sumber_2 adalah tabel BPS jumlah penduduk dan "
        "Ambil_Data_2 berisi 'Jumlah penduduk Provinsi Kalimantan Selatan'."
    )