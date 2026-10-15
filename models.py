import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any


def normalize_kode(value) -> str:
    """
    Kode indikator wajib teks seperti "3.2", "33.a".

    Jika Sheets telanjur mengubah kode menjadi tanggal (mis. "3.2" ->
    3 Februari 2026), Apps Script mengirimnya sebagai Date yang di-JSON
    menjadi UTC, contoh "2026-02-02T16:00:00.000Z" (= 3 Feb 00:00 WITA).
    Nilai demikian dikembalikan menjadi "d.m" berdasarkan tanggal lokal
    WITA (Kalimantan Selatan, UTC+8), sesuai locale parse Sheets.
    """
    text = str(value or "").strip()

    if not text:
        return ""

    match = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})",
        text,
    )

    if match:
        try:
            utc = datetime(
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
                int(match.group(4)),
                int(match.group(5)),
                int(match.group(6)),
                tzinfo=timezone.utc,
            )

            local = utc + timedelta(hours=8)  # WITA

            kode = f"{local.day}.{local.month}"

            # Kode dengan tahun, mis. "3.20" ter-parse jadi 3 Feb 2020.
            if local.year != datetime.now().year:
                kode += f".{str(local.year)[2:]}"

            return kode

        except ValueError:
            return text

    return text


# ============================================================
# STATUS SUMBER / HASIL
# ============================================================
# SUCCESS              -> berhasil diekstrak, nilai valid
# REVIEW               -> ditemukan tapi confidence sedang, perlu review
# NEED_PARSER          -> tidak bisa diparse sama sekali (format tak dikenal)
# LINK_TIDAK_LENGKAP   -> URL hanya portal/halaman daftar, bukan data langsung
# DATA_TIDAK_ADA       -> data memang tidak tersedia di sumber utk tahun/scope
# SUMBER_PERLU_LOGIN   -> sumber butuh autentikasi (401) atau akses khusus
# SUMBER_TERBLOKIR     -> proteksi keamanan (Cloudflare 403) memblokir akses
# UNAVAILABLE          -> (legacy) data tidak tersedia utk tahun tsb

STATUS_SUKSES = "SUCCESS"
STATUS_REVIEW = "REVIEW"
STATUS_NEED_PARSER = "NEED_PARSER"
STATUS_LINK_TIDAK_LENGKAP = "LINK_TIDAK_LENGKAP"
STATUS_DATA_TIDAK_ADA = "DATA_TIDAK_ADA"
STATUS_SUMBER_PERLU_LOGIN = "SUMBER_PERLU_LOGIN"
STATUS_SUMBER_TERBLOKIR = "SUMBER_TERBLOKIR"

# Status yang TIDAK menghasilkan nilai (dianggap gagal per-sumber).
STATUS_TANPA_NILAI = {
    STATUS_NEED_PARSER,
    STATUS_LINK_TIDAK_LENGKAP,
    STATUS_DATA_TIDAK_ADA,
    STATUS_SUMBER_PERLU_LOGIN,
    STATUS_SUMBER_TERBLOKIR,
}


@dataclass
class ExtractionResult:
    status: str
    value: Any = None
    confidence: float = 0.0
    source_type: str = ""
    message: str = ""
    evidence: list[str] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return self.status == STATUS_SUKSES

    @property
    def tanpa_nilai(self) -> bool:
        return self.status in STATUS_TANPA_NILAI


@dataclass
class IndicatorJob:
    job_id: str
    kode: str
    nama_indikator: str
    tahun: str
    wilayah: str
    sumber_1: str = ""
    data_diambil_1: str = ""
    sumber_2: str = ""
    data_diambil_2: str = ""
    sumber_3: str = ""
    data_diambil_3: str = ""
    rumus: str = "S1"

    @classmethod
    def from_dict(cls, data: dict):
        def get(*names, default=""):
            for name in names:
                if name in data and data[name] is not None:
                    return str(data[name]).strip()
            return default

        return cls(
            job_id=get("Job_ID", "job_id"),
            kode=normalize_kode(get("Kode", "kode")),
            nama_indikator=get("Nama_Indikator", "nama_indikator"),
            tahun=get("Tahun", "tahun"),
            wilayah=get("Wilayah", "wilayah"),
            sumber_1=get("Sumber_1", "sumber_1"),
            data_diambil_1=get("Data_Diambil_1", "data_diambil_1"),
            sumber_2=get("Sumber_2", "sumber_2"),
            data_diambil_2=get("Data_Diambil_2", "data_diambil_2"),
            sumber_3=get("Sumber_3", "sumber_3"),
            data_diambil_3=get("Data_Diambil_3", "data_diambil_3"),
            rumus=get("Rumus", "rumus", default="S1") or "S1",
        )
