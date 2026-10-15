import importlib
import re
import os
import sys
import threading
from contextlib import contextmanager

from config import LEGACY_INDICATORS_PATH
from models import ExtractionResult
from sources.iklh_source import scrape_iklh
from sources.aki_source import scrape_aki
from sources.tbc_source import scrape_tbc
from sources.tst_source import scrape_tst

_LEGACY_PATH_LOCK = threading.Lock()


@contextmanager
def _legacy_path():
    path = os.path.abspath(LEGACY_INDICATORS_PATH)
    inserted = False

    # sys.path adalah state global; kunci aksesnya agar aman multi-thread.
    with _LEGACY_PATH_LOCK:
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
            inserted = True

        try:
            yield
        finally:
            if inserted and path in sys.path:
                sys.path.remove(path)


def _ok(value, source):
    from sources.source_resolver import pop_default_used

    default_url = pop_default_used()

    message = "Menggunakan source parser stabil."
    evidence = []

    if default_url:
        message = (
            "Sumber_1 bukan URL yang valid. "
            f"Memakai sumber standar: {default_url}"
        )
        evidence = [f"used_default={default_url}"]

    # Adapter kadang mengembalikan mapping/list kosong ketika data untuk
    # wilayah/tahun yang diminta memang tidak ada. Itu bukan SUCCESS:
    # tanpa nilai apa pun, run_indicator tidak bisa menghitung rumus.
    if value is None or (
        isinstance(value, (dict, list)) and not value
    ):
        return ExtractionResult(
            status="DATA_TIDAK_ADA",
            value=None,
            confidence=0.0,
            source_type=f"legacy:{source}",
            message=(
                "Data tidak tersedia untuk wilayah/tahun yang diminta."
            ),
            evidence=evidence,
        )

    return ExtractionResult(
        status="SUCCESS",
        value=value,
        confidence=1.0,
        source_type=f"legacy:{source}",
        message=message,
        evidence=evidence,
    )


def _error_result(error, source, status="LEGACY_FAILED", confidence=0.0):
    """
    Buat ExtractionResult dari exception adapter.

    Konversi otomatis berdasarkan pesan error:
      - SOURCE_UNAVAILABLE / *_UnavailableError -> DATA_TIDAK_ADA
      - SOURCE_BLOCKED / Cloudflare / 403       -> SUMBER_TERBLOKIR
      - SOURCE_NEED_LOGIN / 401                 -> SUMBER_PERLU_LOGIN
      - SOURCE_LINK_TIDAK_LENGKAP               -> LINK_TIDAK_LENGKAP
    """
    text = str(error)
    first_line = text.splitlines()[0][:300]

    detected = status

    if (
        "SOURCE_UNAVAILABLE" in text
        or "UnavailableError" in type(error).__name__
        or "tidak tersedia" in text
        or "Dataset 107 getData() tidak memiliki row" in text
        or "KALIMANTAN SELATAN tidak ditemukan pada row Dataset 107"
        in text
        or "tidak memiliki 12 key bulan" in text
    ):
        detected = "DATA_TIDAK_ADA"

    elif (
        "SOURCE_BLOCKED" in text
        or "Cloudflare" in text
        or "403 Client Error" in text
        or "terblokir" in text.lower()
    ):
        detected = "SUMBER_TERBLOKIR"

    elif (
        "SOURCE_NEED_LOGIN" in text
        or "401" in text
        or "Authentication" in text
        or "autentikasi" in text
        or "login" in text.lower()
        or "Unauthorized" in text
    ):
        detected = "SUMBER_PERLU_LOGIN"

    elif (
        "SOURCE_LINK_TIDAK_LENGKAP" in text
        or "tidak lengkap" in text
        or "bukan data langsung" in text
    ):
        detected = "LINK_TIDAK_LENGKAP"

    return ExtractionResult(
        status=detected,
        confidence=confidence,
        source_type=f"legacy:{source}",
        message=first_line,
    )


def try_legacy_source(
    url,
    tahun,
    description,
    wilayah=None,
    kode=None,
    nama_indikator=None,
    sumber_2=None,
    sumber_3=None,
):
    lower_url = str(url or "").lower()
    lower_description = str(description or "").lower()
    kode_norm = str(kode or "").strip().lower()
    nama_norm = str(nama_indikator or "").strip().lower()

    print(
        f"  [LEGACY ROUTER] kode={kode_norm or '-'} | "
        f"nama={nama_indikator or '-'}"
    )

    # ========================================================
    # AKI (2.a) - adapter lokal project Auto.
    # Tidak menjalankan aki.run() lama.
    # ========================================================
    if (
        kode_norm == "2.a"
        or "angka kematian ibu" in nama_norm
        or "angka kematian ibu" in lower_description
        or re.search(r"(^|[^a-z])aki([^a-z]|$)", lower_description)
    ):
        print("  [SOURCE ADAPTER] AKI -> sources.aki_source")
        try:
            return _ok(
                scrape_aki(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "aki",
            )
        except Exception as error:
            return _error_result(error, "aki")

    # ========================================================
    # TBC (3.a) - adapter lokal project Auto.
    # Port dari tbc.py legacy (opendata CSV + PDF Profil Kesehatan).
    # ========================================================
    if (
        kode_norm == "3.a"
        or "tuberkulosis" in nama_norm
        or "tuberkulosis" in lower_description
        or re.search(r"(^|[^a-z])tbc([^a-z]|$)", lower_description)
    ):
        print("  [SOURCE ADAPTER] TBC -> sources.tbc_source")
        try:
            return _ok(
                scrape_tbc(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                    url_2=sumber_2,
                    url_3=sumber_3,
                ),
                "tbc",
            )
        except Exception as error:
            return _error_result(error, "tbc")

    # ========================================================
    # IKLHD / IKLH (Indeks Kualitas Lingkungan Hidup) -
    # satudata.kemenlh.go.id. API publik (tanpa login):
    # portal/datasets + datasets/{id}/table + download.xlsx.
    # ========================================================
    if (
        "satudata.kemenlh.go.id" in lower_url
        or "indeks kualitas lingkungan hidup daerah" in nama_norm
        or "iklhd" in lower_description
    ):
        from sources.satudata_source import scrape_iklhd

        try:
            return _ok(
                scrape_iklhd(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "iklhd",
            )
        except Exception as error:
            return _error_result(error, "iklhd")

    # ========================================================
    # IKLH - adapter lokal project Auto.
    # Tidak menjalankan iklh.run() lama.
    # ========================================================
    if (
        kode_norm == "5.b"
        or "indeks kualitas lingkungan hidup" in nama_norm
        or "iklh" in lower_description
        or "indeks kualitas lingkungan hidup" in lower_description
        or "b43f5eef-565f-43f8-9f80-0aa9e0c079eb" in lower_url
    ):
        try:
            return _ok(
                scrape_iklh(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "iklh",
            )
        except Exception as error:
            return _error_result(error, "iklh")

    # ========================================================
    # IPKK (Indeks Pembangunan Kualitas Keluarga) - adapter
    # lokal project Auto. Sumber: kemenpppa.go.id (saat ini
    # maintenance, hanya homepage).
    # Ditempatkan SEBELUM IPK karena singkatan "IPK" juga cocok
    # dengan IPKK; pastikan indikator keluarga tidak tertangkap
    # oleh adapter Pembangunan Kebudayaan.
    # ========================================================
    if (
        kode_norm == "39"
        or "kualitas keluarga" in nama_norm
        or "kualitas keluarga" in lower_description
        or re.search(r"(^|[^a-z])ipkk([^a-z]|$)", lower_description)
    ):
        from sources.ipkk_source import scrape_ipkk

        try:
            return _ok(
                scrape_ipkk(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "ipkk",
            )
        except Exception as error:
            return _error_result(error, "ipkk")

    # ========================================================
    # IPK (Indeks Pembangunan Kebudayaan) - adapter lokal
    # project Auto. Sumber: tabel HTML ipk.kemenbud.go.id.
    # ========================================================
    if (
        "pembangunan kebudayaan" in nama_norm
        or "pembangunan kebudayaan" in lower_description
        or (
            "ipk" in lower_description
            and "kualitas keluarga" not in lower_description
        )
    ):
        from sources.ipk_source import scrape_ipk

        try:
            return _ok(
                scrape_ipk(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "ipk",
            )
        except Exception as error:
            return _error_result(error, "ipk")

    # ========================================================
    # IKUB (Indeks Kerukunan Umat Beragama) - adapter lokal
    # project Auto. Sumber: Open Data Jabar (CSV/ZIP).
    # ========================================================
    if (
        kode_norm == "38"
        or "kerukunan umat beragama" in nama_norm
        or "kerukunan umat beragama" in lower_description
        or (
            re.search(r"(^|[^a-z])ikub([^a-z]|$)", lower_description)
            and "kerukunan" in lower_description
        )
    ):
        from sources.ikub_source import scrape_ikub

        try:
            return _ok(
                scrape_ikub(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "ikub",
            )
        except Exception as error:
            return _error_result(error, "ikub")

    # ========================================================
    # TST (Timbulan Sampah Terolah di Fasilitas Pengolahan
    # Sampah) - adapter lokal project Auto.
    # Sumber: SIPSN sampahnasional.kemenlh.go.id (API v2).
    # ========================================================
    if (
        "timbulan sampah" in nama_norm
        or "timbulan sampah" in lower_description
        or "fasilitas pengolahan sampah" in lower_description
        or "sampahnasional" in lower_url
        or "tst" in lower_description
    ):
        try:
            return _ok(
                scrape_tst(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "tst",
            )
        except Exception as error:
            return _error_result(error, "tst")

    # ========================================================
    # IKG (Indeks Ketimpangan Gender) - adapter lokal project
    # Auto. Sumber: BRS BPS Kalsel (listing keyword=ikg + PDF).
    # ========================================================
    if (
        kode_norm == "40"
        or "ketimpangan gender" in nama_norm
        or "ketimpangan gender" in lower_description
        or (
            re.search(r"(^|[^a-z])ikg([^a-z]|$)", lower_description)
            and "gender" in lower_description
        )
    ):
        from sources.ikg_source import scrape_ikg

        try:
            return _ok(
                scrape_ikg(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "ikg",
            )
        except Exception as error:
            return _error_result(error, "ikg")

    # ========================================================
    # PENUMPANG UDARA (50) - adapter khusus kode=50.
    # Sumber: BPS (bps.go.id). Tabel BPS modern menyematkan
    # data sebagai JSON di dalam HTML.
    # ========================================================
    if (
        kode_norm == "50"
        or "penumpang angkutan udara" in nama_norm
        or "penumpang angkutan udara" in lower_description
        or (
            "penumpang" in lower_description
            and ("udara" in lower_description
                 or "penerbangan" in lower_description)
        )
    ):
        from sources.udara_source import scrape_penumpang_udara

        try:
            return _ok(
                scrape_penumpang_udara(
                    url=url,
                    tahun=tahun,
                    description=description,
                    wilayah=wilayah,
                ),
                "udara",
            )
        except Exception as error:
            return _error_result(error, "udara")

    # ========================================================
    # NILAI TUKAR PETANI / NTP (23.5 / 51) - adapter khusus.
    # Sumber: BPS (bps.go.id). Tabel BPS modern menyematkan
    # data sebagai JSON di dalam HTML. NTP adalah indeks (~70-150).
    # ========================================================
    if (
        kode_norm in ("23.5", "51")
        or "nilai tukar petani" in nama_norm
        or "nilai tukar petani" in lower_description
        or "ntp" in lower_description
        or (
            "tukar" in lower_description
            and "petani" in lower_description
        )
    ):
        from sources.ntp_source import try_ntp_source

        try:
            ntp_result = try_ntp_source(
                url=url,
                tahun=tahun,
                description=description,
                wilayah=wilayah,
            )

            if ntp_result is not None:
                return ntp_result

        except Exception as error:
            return _error_result(error, "ntp")

    if not os.path.isdir(LEGACY_INDICATORS_PATH):
        return None

    try:
        with _legacy_path():

            if "bi.go.id" in lower_url:
                if (
                    "tabel1_12" in lower_url
                    or "tabel1.12" in lower_url
                    or "total kredit" in lower_description
                    or (
                        "kredit" in lower_description
                        and "provinsi" in lower_description
                    )
                ):
                    mod = importlib.import_module("bi_i12")

                    return _ok(
                        mod.scrape_bi_i12_credit(
                            url=url,
                            tahun=tahun,
                            description=description,
                        ),
                        "bi_i12",
                    )

                if (
                    "tabel1_19" in lower_url
                    or "tabel1.19" in lower_url
                    or "dpk" in lower_description
                    or "dana pihak ketiga" in lower_description
                ):
                    mod = importlib.import_module("bi_i19")

                    return _ok(
                        mod.scrape_bi_i19(
                            url=url,
                            tahun=tahun,
                            description=description,
                        ),
                        "bi_i19",
                    )

            if "data.ojk.go.id" in lower_url:
                mod = importlib.import_module("ojk")
                dataset_id = mod.ojk_dataset_id_from_url(url)

                if (
                    dataset_id == "277"
                    or "transaksi saham" in lower_description
                ):
                    # Adapter lokal: ekstrak SEMUA wilayah (provinsi +
                    # kab/kota), bukan hanya Kalimantan Selatan.
                    from sources.ojk_saham_source import (
                        scrape_ojk_saham_annual,
                    )

                    value = scrape_ojk_saham_annual(
                        url=url,
                        tahun=tahun,
                        description=description,
                        wilayah=wilayah,
                    )
                else:
                    value = mod.scrape_ojk_dataset(
                        url=url,
                        tahun=tahun,
                        description=description,
                    )

                return _ok(
                    value,
                    "ojk",
                )

            if "bps.go.id" in lower_url:
                if (
                    "jumlah penduduk" in lower_description
                    or (
                        "penduduk" in lower_description
                        and "pdrb" not in lower_description
                        and "produk domestik regional bruto"
                        not in lower_description
                    )
                ):
                    mod = importlib.import_module("bps")

                    value = mod.scrape_bps_population(
                        url=url,
                        tahun=tahun,
                        description=description,
                    )

                    return _ok(
                        value,
                        "bps_population",
                    )

                if (
                    "pdrb" in lower_description
                    or "produk domestik regional bruto"
                    in lower_description
                ):
                    # Adapter lokal (urllib): tabel BPS baru menyematkan
                    # data sebagai JSON dan situs memblokir Chrome
                    # headless dengan Cloudflare, jadi scraper Selenium
                    # legacy tidak lagi bisa dipakai di sini.
                    from sources.bps_pdrb_source import (
                        scrape_bps_pdrb_page,
                    )

                    try:
                        value = scrape_bps_pdrb_page(
                            url=url,
                            tahun=tahun,
                            description=description,
                        )

                        return _ok(
                            value,
                            "bps_pdrb",
                        )

                    except Exception as adapter_error:
                        return _error_result(adapter_error, "bps_pdrb")

                # ============================================================
                # BPS TEMPLATE: Auto-handle SEMUA indikator BPS lainnya.
                # Jika tidak ada adapter khusus yang match, gunakan template.
                # ============================================================
                if "/statistics-table/" in lower_url:
                    from sources.bps_table_adapter import try_bps_table

                    try:
                        template_result = try_bps_table(
                            url=url,
                            tahun=tahun,
                            description=description,
                            wilayah=wilayah,
                        )

                        if template_result is not None:
                            return template_result

                    except Exception as error:
                        return _error_result(error, "bps_table_template")

                # PDF adapter: handle /pressrelease/, /subject/, atau halaman BPS lainnya.
                from sources.bps_pdf_adapter import try_bps_pdf

                try:
                    template_result = try_bps_pdf(
                        url=url,
                        tahun=tahun,
                        description=description,
                        wilayah=wilayah,
                    )

                    if template_result is not None:
                        return template_result

                except Exception as error:
                    return _error_result(error, "bps_pdf_template")

            if (
                "bappenas.go.id" in lower_url
                or "ibei" in lower_description
                or "indeks ekonomi biru" in lower_description
            ):
                mod = importlib.import_module("ibei")

                return _ok(
                    mod.scrape_ibei(
                        url=url,
                        tahun=tahun,
                        description=description,
                    ),
                    "ibei",
                )

            if (
                "stunting" in lower_description
                or "ssgi" in lower_description
                or "status gizi" in lower_description
            ):
                from sources.stunting_source import (
                    StuntingUnavailableError,
                    scrape_stunting,
                )

                try:
                    value = scrape_stunting(
                        url=url,
                        tahun=tahun,
                        description=description,
                    )

                    return _ok(
                        value,
                        "stunting",
                    )

                except StuntingUnavailableError as error:
                    return _error_result(error, "stunting")

            if (
                "brin.go.id" in lower_url
                or "penerbit.brin.go.id" in lower_url
            ):
                mod = importlib.import_module("brin")

                return _ok(
                    mod.scrape_brin_source(
                        url=url,
                        tahun=tahun,
                        description=description,
                        wilayah=wilayah,
                    ),
                    "brin",
                )

    except Exception as error:
        return _error_result(error, "legacy")

    return None
