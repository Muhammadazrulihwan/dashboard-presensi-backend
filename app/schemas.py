from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class ValidationErrorItem(BaseModel):
    row: int
    column: str
    message: str


class PreviewResponse(BaseModel):
    upload_id: int
    filename: str
    row_count: int
    preview_rows: list[dict]
    errors: list[ValidationErrorItem]
    error_count: int
    can_save: bool
    conflict_count: int = 0
    date_from: date | None = None
    date_to: date | None = None
    employee_count: int = 0
    mapped_columns: list[str] = []


class UploadFileOut(BaseModel):
    jenis_sumber: str  # activity | report | timeoff | approval
    nama_file: str
    row_count: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    warnings: list[str] = []


class UploadOut(BaseModel):
    id: int
    filename: str
    uploader: str
    created_at: datetime
    row_count: int | None
    status: str
    # PRD F16: periode data paket dan rincian per sumber.
    date_from: date | None = None
    date_to: date | None = None
    sumber: list[UploadFileOut] = []


class ConfirmRequest(BaseModel):
    mode: Literal["overwrite", "skip"] | None = None


class ConfirmResponse(BaseModel):
    upload: UploadOut
    inserted: int
    overwritten: int
    skipped: int


class SummaryData(BaseModel):
    hari_kerja: int
    hadir: int
    kehadiran_pct: float | None
    terlambat: int
    menit_terlambat: int
    izin: int
    sakit: int
    cuti: int
    alpa: int
    dinas_luar: int
    tidak_hadir: int
    avg_jam_masuk: str | None
    avg_jam_masuk_menit: float | None
    avg_selisih_masuk_menit: float | None = None
    karyawan: int
    records: int
    # Rekap terpadu (Rilis B)
    dikoreksi: int = 0
    selisih_report: int = 0
    clock_out_hilang: int = 0
    koreksi_setelah_tutup: int = 0
    jam_kerja_nyata: float | None = None


class SummaryResponse(BaseModel):
    current: SummaryData
    previous: SummaryData | None


class TrendPoint(BaseModel):
    period: str
    kehadiran_pct: float | None
    terlambat: int


class StatusCount(BaseModel):
    status: str
    total: int


class DeptRow(BaseModel):
    departemen: str
    karyawan: int
    hari_kerja: int
    hadir: int
    kehadiran_pct: float | None
    terlambat: int
    menit_terlambat: int


class TopEmployee(BaseModel):
    nik: str
    nama: str
    departemen: str
    terlambat: int
    menit_terlambat: int
    alpa: int


class AttendanceOut(BaseModel):
    id: int
    tanggal: date
    nik: str
    nama: str
    departemen: str
    jam_masuk: str | None
    jam_keluar: str | None
    menit_terlambat: int
    durasi_kerja: float | None
    status: str
    keterangan: str | None
    # Rekap terpadu (Rilis B)
    jam_kerja_nyata: float | None = None
    dikoreksi: bool = False
    koreksi_alasan: str | None = None
    koreksi_approver: str | None = None
    clock_out_hilang: bool = False
    selisih_report: str | None = None
    # Jejak sumber dan izin (PRD Bab 10 "Kolom sumber, koreksi, izin, dan tanda selisih").
    sumber: str | None = None
    jumlah_ketukan: int | None = None
    clock_in_hilang: bool = False
    izin_kode: str | None = None
    izin_nama: str | None = None
    izin_tipe: str | None = None


class RecordsPage(BaseModel):
    items: list[AttendanceOut]
    total: int
    page: int
    page_size: int


class MetaOut(BaseModel):
    min_date: date | None
    max_date: date | None
    departemen: list[str]
    statuses: list[str]


class EmployeeTrendPoint(BaseModel):
    period: str
    status: str
    kehadiran_pct: float | None
    terlambat: int


class EmployeeDetail(BaseModel):
    nik: str
    nama: str
    departemen: str
    rekap: SummaryData
    range: list[AttendanceOut]
    trend: list[EmployeeTrendPoint]


# ========== Kelengkapan Data (PRD F12) ==========

class CompletenessPeriode(BaseModel):
    from_: date | None = Field(None, alias="from")
    to: date | None = None
    model_config = {"populate_by_name": True}


class CompletenessRingkasan(BaseModel):
    karyawan: int
    karyawan_lengkap: int
    karyawan_tanpa_data: int
    hari_tanpa_data: int
    baris_clock_out_hilang: int
    baris_clock_in_hilang: int
    baris_selisih_report: int = 0
    baris_koreksi_setelah_tutup: int = 0


class CompletenessTanpaData(BaseModel):
    nik: str
    nama: str
    departemen: str
    hari_kerja_tercatat: int
    hari_tanpa_data: int


class CompletenessKetukan(BaseModel):
    nik: str
    nama: str
    departemen: str
    tanggal: date
    jam_masuk: str | None
    jam_keluar: str | None


class CompletenessCakupan(BaseModel):
    periode: str
    karyawan: int
    baris: int
    hari_kerja: int


class CompletenessSumber(BaseModel):
    kode: str
    nama: str
    status: str  # "tersedia" | "belum"
    catatan: str


class CompletenessSelisih(BaseModel):
    nik: str
    nama: str
    departemen: str
    tanggal: date
    selisih: str
    menit_terlambat: int


class CompletenessKoreksiTutup(BaseModel):
    nik: str
    nama: str
    departemen: str
    tanggal: date
    alasan: str | None
    approver: str | None


class CompletenessResponse(BaseModel):
    periode: CompletenessPeriode
    hari_kerja_kalender: int
    ringkasan: CompletenessRingkasan
    karyawan_tanpa_data: list[CompletenessTanpaData]
    clock_out_hilang: list[CompletenessKetukan]
    clock_in_hilang: list[CompletenessKetukan]
    selisih_report: list[CompletenessSelisih] = []
    koreksi_setelah_tutup: list[CompletenessKoreksiTutup] = []
    cakupan_bulan: list[CompletenessCakupan]
    sumber: list[CompletenessSumber]


class ShiftIn(BaseModel):
    jam_masuk: str = Field(pattern=r"^\d{2}:\d{2}$")
    jam_pulang: str = Field(pattern=r"^\d{2}:\d{2}$")
    toleransi_menit: int = Field(0, ge=0, le=240)


class ShiftOut(BaseModel):
    kode: str
    jam_masuk: str
    jam_pulang: str
    toleransi_menit: int
    lintas_hari: bool


class HolidayIn(BaseModel):
    tanggal: date
    nama: str = Field(min_length=1, max_length=150)


class HolidayOut(BaseModel):
    id: int
    tanggal: date
    nama: str


class RecalcRequest(BaseModel):
    date_from: date = Field(alias="from")
    date_to: date = Field(alias="to")
    model_config = {"populate_by_name": True}


class RecalcResponse(BaseModel):
    updated: int


class PayrollSettingsIn(BaseModel):
    payroll_workdays: int = Field(ge=1, le=31)


class PayrollSettingsOut(BaseModel):
    payroll_workdays: int


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    departemen: str | None
    is_approver: bool
    is_active: bool
    created_at: datetime


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    full_name: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=8, max_length=200)
    departemen: str | None = Field(None, max_length=40)


class UserUpdate(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=150)
    departemen: str | None = Field(None, max_length=40)
    is_approver: bool | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    password: str = Field(min_length=8, max_length=200)


# ========== Kinerja Penjualan (Rilis E) ==========

class SalesValidationError(BaseModel):
    sheet: str
    row: int
    column: str
    message: str


class SalesReuploadInfo(BaseModel):
    """Data yang sudah ada dan akan diganti oleh file ini, per periode."""
    periode: str
    cabang: list[str]
    penjualan_diganti: int
    penilaian_diputuskan: int  # Disetujui atau Ditolak yang akan kembali ke Draf


class SalesPreviewResponse(BaseModel):
    upload_id: int
    filename: str
    row_count_penjualan: int
    row_count_target: int
    errors: list[SalesValidationError]
    error_count: int
    warnings: list[str]
    can_save: bool
    reupload: list[SalesReuploadInfo] = []
    perlu_konfirmasi: bool = False


class SalesUploadOut(BaseModel):
    id: int
    filename: str
    uploader: str
    created_at: datetime
    row_count: int | None
    status: str


class SalesConfirmResponse(BaseModel):
    upload: SalesUploadOut
    inserted_performance: int
    inserted_targets: int
    assessments_created: int
    assessments_reset: int = 0
    assessments_removed: int = 0


class SalesmanPerformance(BaseModel):
    employee_id: str
    nama: str
    cabang: str
    periode: str
    penjualan: float
    penerimaan: float | None
    piutang_outlet: float | None
    sisa_bad_debt: float | None
    target: float | None
    capaian_pct: float | None
    kontribusi_cabang_pct: float | None
    peringkat_cabang: int | None
    status_kelayakan: str  # "Layak", "Tidak Layak", "Target belum ada"
    assessment_status: str  # "Draf", "Disetujui", "Ditolak"
    approved_by: str | None
    approved_at: datetime | None
    catatan: str | None = None  # catatan penyetuju pada keputusan terakhir
    # Kehadiran dari data presensi periode yang sama (PRD F31). None bila NIK tidak ada di data presensi.
    hari_kerja: int | None = None
    hari_hadir: int | None = None
    kehadiran_pct: float | None = None
    terlambat_hari: int | None = None


class CabangPerformance(BaseModel):
    cabang: str
    periode: str
    granularitas: str = "bulan"  # bulan | kuartal | tahun
    total_penjualan: float
    target_cabang: float | None
    capaian_pct: float | None
    sisa_target: float
    jumlah_salesman: int
    salesman_layak: int
    salesman_tidak_layak: int
    peringkat: int | None
    status: str  # "Tercapai", "Perlu dikejar", "Jauh dari target"
    # PRD F35: nama salesman yang belum layak pada cabang ini (bulan terakhir periode).
    salesman_belum_layak: list[str] = []
    # Kebutuhan penjualan per hari kerja untuk menutup sisa target (bila sisa hari kerja diketahui).
    sisa_hari_kerja: int | None = None
    kebutuhan_per_hari_kerja: float | None = None


class PerusahaanPerformance(BaseModel):
    periode: str
    granularitas: str = "bulan"  # bulan | kuartal | tahun
    total_penjualan: float
    target_perusahaan: float | None
    capaian_pct: float | None
    sisa_target: float
    jumlah_cabang: int
    cabang_tercapai: int
    # PRD F35: daftar cabang yang belum mencapai target, terurut capaian terendah.
    cabang_belum_tercapai: list[str] = []


class AssessmentUpdate(BaseModel):
    status: str = Field(pattern="^(Draf|Disetujui|Ditolak)$")
    notes: str | None = Field(None, max_length=500)


# ========== Arsip file upload (PRD F22-F24) ==========

class ArchivedFileOut(BaseModel):
    id: int
    upload_id: int | None
    jenis_sumber: str
    nama_asli: str
    ukuran: int
    checksum: str
    checksum_terverifikasi: bool
    pengunggah: str | None
    periode_awal: date | None
    periode_akhir: date | None
    status: str  # aktif | sampah | dihapus
    created_at: datetime
    dihapus_at: datetime | None
    alasan_hapus: str | None
    bisa_dihapus_permanen: bool
    # Employee ID yang tercatat pada file ini (PRD F23). Bisa kosong untuk arsip lama sebelum fitur ini.
    employee_ids: list[str] = []


class ArchivedFilePage(BaseModel):
    items: list[ArchivedFileOut]
    total: int
    page: int
    page_size: int


class TrashRequest(BaseModel):
    alasan: str = Field(min_length=1, max_length=500)


class BulkDeleteRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    alasan: str | None = Field(None, max_length=500)


class BulkDeleteResponse(BaseModel):
    dihapus: int
    tidak_ditemukan: list[int] = []
    arsip_ke_tempat_sampah: int = 0


# ========== Paket empat sumber (Rilis B) ==========

class SourceFileInfo(BaseModel):
    jenis_sumber: str  # activity | report | timeoff | approval
    nama_file: str
    row_count: int
    warnings: list[str] = []
    mapped_columns: list[str] = []
    date_from: date | None = None
    date_to: date | None = None


class PackagePreviewResponse(BaseModel):
    upload_id: int
    files: list[SourceFileInfo]
    errors: list[str] = []
    can_save: bool
    date_from: date | None = None
    date_to: date | None = None
    employee_count: int = 0
    rekap_rows: int = 0
    status_counts: dict[str, int] = {}
    conflict_count: int = 0


class PackageConfirmRequest(BaseModel):
    mode: Literal["overwrite", "skip"] | None = None


class PackageConfirmResponse(BaseModel):
    upload: UploadOut
    inserted: int
    overwritten: int
    skipped: int
    sumber: dict[str, int] = {}
    employees: int = 0


# ========== Tutup/buka periode (PRD F13) ==========

class PeriodOut(BaseModel):
    tahun: int
    bulan: int
    periode: str  # "YYYY-MM"
    ditutup: bool
    ditutup_oleh: str | None = None
    ditutup_at: datetime | None = None
    alasan: str | None = None
    # Jumlah baris rekap pada bulan itu, untuk memberi konteks sebelum menutup.
    records: int = 0


class PeriodCloseRequest(BaseModel):
    tahun: int = Field(ge=2000, le=2100)
    bulan: int = Field(ge=1, le=12)
    alasan: str | None = Field(None, max_length=500)


class PeriodReopenRequest(BaseModel):
    tahun: int = Field(ge=2000, le=2100)
    bulan: int = Field(ge=1, le=12)
    alasan: str = Field(min_length=1, max_length=500)


# ========== Kinerja Karyawan (PRD F26) ==========

class PerformanceRow(BaseModel):
    nik: str
    nama: str
    departemen: str
    hari_kerja: int
    hadir: int
    dinas_luar: int
    kehadiran_pct: float | None
    terlambat: int
    menit_terlambat: int
    tepat_waktu_pct: float | None
    izin: int
    sakit: int
    cuti: int
    alpa: int
    pulang_cepat: int
    jam_kerja: float
    menit_lembur: int
    dikoreksi: int

# ========== Master karyawan (PRD F15) ==========

class EmployeeOut(BaseModel):
    employee_id: str
    nama: str
    jabatan: str | None = None
    organisasi: str | None = None
    cabang: str | None = None
    status_kerja: str | None = None
    resign: bool = False
    updated_at: datetime | None = None


class EmployeePage(BaseModel):
    items: list[EmployeeOut]
    total: int
    page: int
    page_size: int


class EmployeeMeta(BaseModel):
    organisasi: list[str] = []
    cabang: list[str] = []
    resign_count: int = 0
    total: int = 0
