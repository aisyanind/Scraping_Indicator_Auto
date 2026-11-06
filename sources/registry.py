"""
Registry adapter sumber — daftar deklaratif pengganti if-chain.

Menambahkan indikator/situs baru cukup menambah satu entri AdapterSpec
di ADAPTERS (predicate Clause + handler run), tanpa mengubah fungsi
dispatcher. Urutan entri = urutan prioritas (pertama yang cocok menang).

Struktur:
- Clause       : predicate deklaratif (substring/regex pada nama,
                 deskripsi, URL; opsional ekstra callable).
- AdapterSpec  : nama adapter + daftar Clause (OR) + handler + flag
                 apakah butuh folder legacy_indicators/.
- run_adapters : loop registry, wrap hasil dengan _ok/_error_result.
"""

import importlib
import os
import re
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Optional

from models import ExtractionResult
from sources.source_resolver import pop_default_used

_LEGACY_PATH_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------

@dataclass
class AdapterContext:
    """Input mentah untuk matcher & handler adapter."""

    url: object = None
    tahun: object = None
    description: object = None
    wilayah: object = None
    kode: object = None
    nama_indikator: object = None
    sumber_2: object = None
    sumber_3: object = None

    lower_url: str = ""
    lower_description: str = ""
    lower_nama: str = ""

    def __post_init__(self):
        self.lower_url = str(self.url or "").lower()
        self.lower_description = str(self.description or "").lower()
        self.lower_nama = str(self.nama_indikator or "").strip().lower()


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Clause:
    """
    Satu syarat matcher. Semua field yang diisi WAJIB terpenuhi (AND);
    dalam satu field beberapa nilai dianggap OR.

    - text_contains     : substring cocok di nama_indikator ATAU deskripsi
    - name_contains     : substring cocok di nama_indikator
    - desc_contains     : substring cocok di deskripsi
    - desc_not_contains : tidak boleh ada substring ini di deskripsi
    - desc_regex        : regex cocok di deskripsi
    - url_contains      : substring cocok di URL
    - url_not_contains  : tidak boleh ada substring ini di URL
    - extra             : predicate tambahan (AND)
    """

    text_contains: tuple = ()
    name_contains: tuple = ()
    desc_contains: tuple = ()
    desc_not_contains: tuple = ()
    desc_regex: tuple = ()
    url_contains: tuple = ()
    url_not_contains: tuple = ()
    extra: Optional[Callable[[AdapterContext], bool]] = None

    def matches(self, ctx: AdapterContext) -> bool:
        if self.text_contains and not any(
            token in ctx.lower_nama or token in ctx.lower_description
            for token in self.text_contains
        ):
            return False

        if self.name_contains and not any(
            token in ctx.lower_nama
            for token in self.name_contains
        ):
            return False

        if self.desc_contains and not any(
            token in ctx.lower_description
            for token in self.desc_contains
        ):
            return False

        if self.desc_not_contains and any(
            token in ctx.lower_description
            for token in self.desc_not_contains
        ):
            return False

        if self.desc_regex and not any(
            re.search(pattern, ctx.lower_description)
            for pattern in self.desc_regex
        ):
            return False

        if self.url_contains and not any(
            token in ctx.lower_url
            for token in self.url_contains
        ):
            return False

        if self.url_not_contains and any(
            token in ctx.lower_url
            for token in self.url_not_contains
        ):
            return False

        if self.extra is not None and not self.extra(ctx):
            return False

        return True


@dataclass(frozen=True)
class AdapterSpec:
    """Satu entri registry: matcher (OR antar clause) + handler."""

    name: str
    clauses: tuple
    run: Callable[[AdapterContext], object]
    requires_legacy_dir: bool = False

    def matches(self, ctx: AdapterContext) -> bool:
        return any(clause.matches(ctx) for clause in self.clauses)


# ---------------------------------------------------------------------------
# Handler per adapter (lazy import di dalam fungsi)
# ---------------------------------------------------------------------------

def _run_aki(ctx: AdapterContext):
    from sources.aki_source import scrape_aki

    return scrape_aki(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
    )


def _run_tbc(ctx: AdapterContext):
    from sources.tbc_source import scrape_tbc

    return scrape_tbc(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
        url_2=ctx.sumber_2,
        url_3=ctx.sumber_3,
    )


def _run_iklhd(ctx: AdapterContext):
    from sources.satudata_source import scrape_iklhd

    return scrape_iklhd(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
    )


def _run_iklh(ctx: AdapterContext):
    from sources.iklh_source import scrape_iklh

    return scrape_iklh(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
    )


def _run_ipkk(ctx: AdapterContext):
    from sources.ipkk_source import scrape_ipkk

    return scrape_ipkk(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
    )


def _run_ipk(ctx: AdapterContext):
    from sources.ipk_source import scrape_ipk

    return scrape_ipk(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
    )


def _run_ikub(ctx: AdapterContext):
    from sources.ikub_source import scrape_ikub

    return scrape_ikub(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
    )


def _run_tst(ctx: AdapterContext):
    from sources.tst_source import scrape_tst

    return scrape_tst(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
    )


def _run_bi_i12(ctx: AdapterContext):
    mod = importlib.import_module("bi_i12")

    return mod.scrape_bi_i12_credit(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
    )


def _run_bi_i19(ctx: AdapterContext):
    mod = importlib.import_module("bi_i19")

    return mod.scrape_bi_i19(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
    )


def _run_ojk(ctx: AdapterContext):
    mod = importlib.import_module("ojk")
    dataset_id = mod.ojk_dataset_id_from_url(ctx.url)

    if dataset_id == "277" or "transaksi saham" in ctx.lower_description:
        # Adapter lokal: ekstrak SEMUA wilayah (provinsi + kab/kota),
        # bukan hanya Kalimantan Selatan.
        from sources.ojk_saham_source import scrape_ojk_saham_annual

        return scrape_ojk_saham_annual(
            url=ctx.url,
            tahun=ctx.tahun,
            description=ctx.description,
            wilayah=ctx.wilayah,
        )

    return mod.scrape_ojk_dataset(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
    )


def _run_ibei(ctx: AdapterContext):
    mod = importlib.import_module("ibei")

    return mod.scrape_ibei(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
    )


def _run_stunting(ctx: AdapterContext):
    from sources.stunting_source import scrape_stunting

    return scrape_stunting(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
    )


def _run_brin(ctx: AdapterContext):
    mod = importlib.import_module("brin")

    return mod.scrape_brin_source(
        url=ctx.url,
        tahun=ctx.tahun,
        description=ctx.description,
        wilayah=ctx.wilayah,
    )


# ---------------------------------------------------------------------------
# Predicate ekstra (legacy)
# ---------------------------------------------------------------------------

def _not_tbc_jumlah(ctx: AdapterContext) -> bool:
    """
    Adapter tbc hanya menangani indikator CAKUPAN penemuan kasus TB (%).
    Indikator "jumlah kasus" (absolut) harus jatuh ke ekstraktor generik
    (PDF/chart), bukan mengembalikan nilai persen dari opendata.
    """
    text = f"{ctx.lower_nama} {ctx.lower_description}"
    wants_jumlah = any(
        marker in text
        for marker in (
            "jumlah kasus",
            "jumlah seluruh kasus",
            "jumlah seluruh tbc",
            "total kasus",
            "jumlah tb",
            "jumlah tbc",
        )
    )
    if not wants_jumlah:
        return True
    return "cakupan" in text


def _tbc_has_multi_region_source(ctx: AdapterContext) -> bool:
    """
    Data per kab/kota hanya tersedia dari CSV opendata; PDF Profil hanya
    memuat nilai provinsi. Tolak indikator kab/kota ketika tidak ada satu
    pun sumber opendata (mis. PDF saja) supaya gagal cepat dan jelas,
    bukan hijack adapter lalu RuntimeError "data tidak tersedia".
    """
    urls = " ".join(
        str(part or "").lower()
        for part in (ctx.url, ctx.sumber_2, ctx.sumber_3)
    )
    if "opendata.kalselprov.go.id" in urls:
        return True

    text = f"{ctx.lower_nama} {ctx.lower_description}"
    wants_kab_kota = any(
        marker in text
        for marker in (
            "kab/kota",
            "kabupaten/kota",
            "kabupaten dan kota",
            "seluruh kab",
            "semua kab",
            "kab kota",
        )
    )
    return not wants_kab_kota


def _tbc_guard(ctx: AdapterContext) -> bool:
    return _not_tbc_jumlah(ctx) and _tbc_has_multi_region_source(ctx)


def _is_bi_i12(ctx: AdapterContext) -> bool:
    return (
        "tabel1_12" in ctx.lower_url
        or "tabel1.12" in ctx.lower_url
        or "total kredit" in ctx.lower_description
        or (
            "kredit" in ctx.lower_description
            and "provinsi" in ctx.lower_description
        )
    )


def _is_bi_i19(ctx: AdapterContext) -> bool:
    return (
        "tabel1_19" in ctx.lower_url
        or "tabel1.19" in ctx.lower_url
        or "dpk" in ctx.lower_description
        or "dana pihak ketiga" in ctx.lower_description
    )


# ---------------------------------------------------------------------------
# Registry — urutan = prioritas; pertama yang cocok menang.
# ---------------------------------------------------------------------------

ADAPTERS: tuple = (
    # ================================================================
    # Adapter lokal project Auto (bukan modul legacy).
    # ================================================================
    AdapterSpec(
        name="aki",
        clauses=(
            Clause(text_contains=("angka kematian ibu",)),
            Clause(desc_regex=(r"(^|[^a-z])aki([^a-z]|$)",)),
        ),
        run=_run_aki,
    ),
    AdapterSpec(
        name="tbc",
        clauses=(
            Clause(
                text_contains=("tuberkulosis",),
                extra=_tbc_guard,
            ),
            Clause(
                desc_regex=(r"(^|[^a-z])tbc([^a-z]|$)",),
                extra=_tbc_guard,
            ),
        ),
        run=_run_tbc,
    ),
    AdapterSpec(
        name="iklhd",
        clauses=(
            Clause(url_contains=("satudata.kemenlh.go.id",)),
            # Nama "...Lingkungan Hidup Daerah" / kode "iklhd" sering
            # dipakai untuk indikator yang Sumber_1-nya di Open Data
            # Kalsel (bukan satudata.kemenlh). URL non-satudata harus
            # jatuh ke adapter iklh (scrape_iklh), bukan scrape_iklhd
            # yang slug-nya tidak ada di portal satudata.
            Clause(
                name_contains=("indeks kualitas lingkungan hidup daerah",),
                url_not_contains=("opendata.kalselprov.go.id",),
            ),
            Clause(
                desc_contains=("iklhd",),
                url_not_contains=("opendata.kalselprov.go.id",),
            ),
        ),
        run=_run_iklhd,
    ),
    AdapterSpec(
        name="iklh",
        clauses=(
            Clause(
                name_contains=("indeks kualitas lingkungan hidup",)
            ),
            Clause(
                desc_contains=(
                    "iklh",
                    "indeks kualitas lingkungan hidup",
                )
            ),
            Clause(
                url_contains=(
                    "b43f5eef-565f-43f8-9f80-0aa9e0c079eb",
                )
            ),
        ),
        run=_run_iklh,
    ),
    # IPKK SEBELUM IPK: singkatan "IPK" juga cocok dengan "IPKK";
    # pastikan indikator keluarga tidak tertangkap oleh adapter
    # Pembangunan Kebudayaan.
    AdapterSpec(
        name="ipkk",
        clauses=(
            Clause(text_contains=("kualitas keluarga",)),
            Clause(desc_regex=(r"(^|[^a-z])ipkk([^a-z]|$)",)),
        ),
        run=_run_ipkk,
    ),
    AdapterSpec(
        name="ipk",
        clauses=(
            Clause(text_contains=("pembangunan kebudayaan",)),
            Clause(
                desc_contains=("ipk",),
                desc_not_contains=("kualitas keluarga",),
            ),
        ),
        run=_run_ipk,
    ),
    AdapterSpec(
        name="ikub",
        clauses=(
            Clause(text_contains=("kerukunan umat beragama",)),
            Clause(
                desc_regex=(r"(^|[^a-z])ikub([^a-z]|$)",),
                desc_contains=("kerukunan",),
            ),
        ),
        run=_run_ikub,
    ),
    AdapterSpec(
        name="tst",
        clauses=(
            Clause(
                text_contains=(
                    "timbulan sampah",
                    "fasilitas pengolahan sampah",
                )
            ),
            Clause(url_contains=("sampahnasional",)),
            Clause(desc_contains=("tst",)),
        ),
        run=_run_tst,
    ),

    # ================================================================
    # Modul legacy (folder legacy_indicators/).
    # ================================================================
    AdapterSpec(
        name="bi_i12",
        clauses=(
            Clause(url_contains=("bi.go.id",), extra=_is_bi_i12),
        ),
        run=_run_bi_i12,
        requires_legacy_dir=True,
    ),
    AdapterSpec(
        name="bi_i19",
        clauses=(
            Clause(url_contains=("bi.go.id",), extra=_is_bi_i19),
        ),
        run=_run_bi_i19,
        requires_legacy_dir=True,
    ),
    AdapterSpec(
        name="ojk",
        clauses=(
            Clause(url_contains=("data.ojk.go.id",)),
        ),
        run=_run_ojk,
        requires_legacy_dir=True,
    ),
    AdapterSpec(
        name="ibei",
        clauses=(
            Clause(url_contains=("bappenas.go.id",)),
            Clause(
                desc_contains=("ibei", "indeks ekonomi biru")
            ),
        ),
        run=_run_ibei,
        requires_legacy_dir=True,
    ),
    AdapterSpec(
        name="stunting",
        clauses=(
            Clause(
                desc_contains=("stunting", "ssgi", "status gizi")
            ),
        ),
        run=_run_stunting,
    ),
    AdapterSpec(
        name="brin",
        clauses=(
            Clause(
                url_contains=("brin.go.id", "penerbit.brin.go.id")
            ),
        ),
        run=_run_brin,
        requires_legacy_dir=True,
    ),
)


# ---------------------------------------------------------------------------
# Pelaksanaan
# ---------------------------------------------------------------------------

@contextmanager
def _legacy_path(path):
    """Sisipkan folder legacy ke sys.path selama import modul legacy."""
    path = os.path.abspath(str(path or ""))
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


def run_adapters(ctx: AdapterContext, legacy_path=None):
    """
    Cari adapter yang cocok dengan ctx lalu jalankan.

    - Adapter butuh folder legacy dilewati jika legacy_path tidak ada.
    - Hasil handler dibungkus _ok(); exception dibungkus _error_result().
    - Return None jika tidak ada adapter yang cocok.
    """
    legacy_dir_ok = bool(legacy_path) and os.path.isdir(str(legacy_path))

    for spec in ADAPTERS:
        if not spec.matches(ctx):
            continue

        if spec.requires_legacy_dir and not legacy_dir_ok:
            continue

        print(f"  [SOURCE ADAPTER] {spec.name}")

        try:
            if spec.requires_legacy_dir:
                with _legacy_path(legacy_path):
                    value = spec.run(ctx)
            else:
                value = spec.run(ctx)
        except Exception as error:
            return _error_result(error, spec.name)

        return _ok(value, spec.name)

    return None
