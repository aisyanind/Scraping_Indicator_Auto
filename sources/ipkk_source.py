"""
Adapter INDEKS PEMBANGUNAN KUALITAS KELUARGA (IPKK) - Auto project.

Sumber: https://kemenpppa.go.id/ (homepage, site maintenance).

Saat ini situs KemenPPPA sedang pemeliharaan dan tidak ada URL data
spesifik yang diberikan. Adapter mengembalikan LINK_TIDAK_LENGKAP
dengan pesan jelas agar user mengisi link data IPKK yang benar.
"""

PROVINSI_TARGET = "Prov. Kalimantan Selatan"


class LinkTidakLengkapError(RuntimeError):
    """Sumber bukan URL data yang valid."""


def scrape_ipkk(
    url,
    tahun,
    description,
    wilayah=None,
):
    """
    Adapter IPKK untuk project Auto.

    URL sumber saat ini hanya homepage KemenPPPA (site maintenance),
    bukan halaman data IPKK. Kembalikan LINK_TIDAK_LENGKAP.
    """
    raise LinkTidakLengkapError(
        "SOURCE_LINK_TIDAK_LENGKAP: URL sumber hanya homepage "
        "kemenpppa.go.id, bukan halaman data IPKK. Situs KemenPPPA "
        "sedang pemeliharaan. Mohon isi link data IPKK yang benar "
        "(portal data/publikasi) saat situs sudah normal."
    )


__all__ = ["scrape_ipkk"]