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


class UploadOut(BaseModel):
    id: int
    filename: str
    uploader: str
    created_at: datetime
    row_count: int | None
    status: str


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
    total_penjualan: float
    target_cabang: float | None
    capaian_pct: float | None
    sisa_target: float
    jumlah_salesman: int
    salesman_layak: int
    salesman_tidak_layak: int
    peringkat: int | None
    status: str  # "Tercapai", "Perlu dikejar", "Jauh dari target"


class PerusahaanPerformance(BaseModel):
    periode: str
    total_penjualan: float
    target_perusahaan: float | None
    capaian_pct: float | None
    sisa_target: float
    jumlah_cabang: int
    cabang_tercapai: int


class AssessmentUpdate(BaseModel):
    status: str = Field(pattern="^(Draf|Disetujui|Ditolak)$")
    notes: str | None = Field(None, max_length=500)
