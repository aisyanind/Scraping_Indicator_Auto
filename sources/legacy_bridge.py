"""
Jembatan ke adapter sumber.

Dispatcher berbasis registry ada di ``sources/registry.py`` — menambah
indikator baru cukup menambah entri ``AdapterSpec`` di sana, tanpa
mengubah fungsi di modul ini.

Modul ini mempertahankan API lama (``try_legacy_source``, ``_ok``,
``_error_result``) agar ``engine.router`` dan test tetap berfungsi.
"""

from config import LEGACY_INDICATORS_PATH
from sources.registry import (
    AdapterContext,
    _error_result,  # noqa: F401  (re-export untuk test)
    _legacy_path,   # noqa: F401  (re-export untuk kompatibilitas)
    _ok,            # noqa: F401  (re-export untuk test)
    run_adapters,
)

__all__ = [
    "try_legacy_source",
    "_ok",
    "_error_result",
    "_legacy_path",
    "LEGACY_INDICATORS_PATH",
]


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
    print(
        f"  [LEGACY ROUTER] kode={kode or '-'} (context only) | "
        f"nama={nama_indikator or '-'}"
    )

    ctx = AdapterContext(
        url=url,
        tahun=tahun,
        description=description,
        wilayah=wilayah,
        kode=kode,
        nama_indikator=nama_indikator,
        sumber_2=sumber_2,
        sumber_3=sumber_3,
    )

    return run_adapters(ctx, legacy_path=LEGACY_INDICATORS_PATH)
