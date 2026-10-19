import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, Date, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

JsonType = JSON()
BigId = BigInteger().with_variant(Integer(), "sqlite")  # agar autoincrement jalan di SQLite

STATUSES = ["Hadir", "Izin", "Sakit", "Cuti", "Dinas Luar", "Alpa"]  # status dari file
LIBUR = "Libur"  # status hasil hitung: hari Minggu atau hari libur tanpa kehadiran
PRESENT = ("Hadir", "Dinas Luar")  # dihitung hadir pada tingkat kehadiran


class Department(str, enum.Enum):
    """Departemen baku. Hak akses dashboard dan fitur sensitif ditentukan oleh nilai ini."""

    IT = "IT"
    HR = "HR"
    AUDIT = "Audit"
    SALES = "Sales/Marketing"


# Departemen yang boleh membuka dashboard.
DASHBOARD_DEPTS = (Department.IT, Department.HR, Department.AUDIT, Department.SALES)
# Departemen yang boleh menilai kinerja, export rekap individu, dan lihat Rekap Payroll.
PERFORMANCE_DEPTS = (Department.HR, Department.AUDIT, Department.SALES)
HR_DEPTS = (Department.HR,)
IT_DEPTS = (Department.IT,)


class UploadStatus(str, enum.Enum):
    pending = "pending"
    validated = "validated"
    saved = "saved"
    failed = "failed"
    deleted = "deleted"


class Departments(Base):
    """Daftar departemen baku. Pemeriksaan hak akses memakai kode ini, bukan teks bebas."""

    __tablename__ = "departments"
    kode: Mapped[str] = mapped_column(String(40), primary_key=True)
    nama: Mapped[str] = mapped_column(String(100))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    full_name: Mapped[str] = mapped_column(String(150))
    password_hash: Mapped[str] = mapped_column(Text)
    # Hak akses ditentukan departemen (nilai baku), bukan peran. Kosong = tidak boleh membuka dashboard.
    departemen: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Penanda penyetuju untuk akun Sales/Marketing yang boleh menyetujui Penilaian Kinerja Penjualan (F32)
    is_approver: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Upload(Base):
    __tablename__ = "uploads"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[UploadStatus] = mapped_column(Enum(UploadStatus, native_enum=False, length=20), default=UploadStatus.pending)
    error_summary: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ShiftRule(Base):
    """Aturan jam kerja per shift. Jam disimpan sebagai menit sejak 00:00."""

    __tablename__ = "shift_rules"
    kode: Mapped[str] = mapped_column(String(20), primary_key=True)
    jam_masuk_menit: Mapped[int] = mapped_column(Integer)
    jam_pulang_menit: Mapped[int] = mapped_column(Integer)
    toleransi_menit: Mapped[int] = mapped_column(Integer, default=0)


class Holiday(Base):
    __tablename__ = "holidays"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tanggal: Mapped[date] = mapped_column(Date, unique=True)
    nama: Mapped[str] = mapped_column(String(150))


class Attendance(Base):
    """Satu baris per karyawan per tanggal. Kolom hitung diisi sistem saat import atau hitung ulang."""

    __tablename__ = "attendance"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), index=True)
    nik: Mapped[str] = mapped_column(String(30))
    nama: Mapped[str] = mapped_column(String(150))
    tanggal: Mapped[date] = mapped_column(Date)
    periode_ym: Mapped[str] = mapped_column(String(7))  # "2026-10", untuk tren bulanan
    departemen: Mapped[str] = mapped_column(String(100))
    shift: Mapped[str] = mapped_column(String(20))
    jam_masuk_menit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jam_keluar_menit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_file: Mapped[str | None] = mapped_column(String(20), nullable=True)  # status asli dari file, kosong jika tidak diisi
    status: Mapped[str] = mapped_column(String(20))  # status akhir hasil hitung
    keterangan: Mapped[str | None] = mapped_column(Text, nullable=True)
    lokasi_kerja: Mapped[str | None] = mapped_column(String(100), nullable=True)
    hari_kerja: Mapped[bool] = mapped_column(Boolean)
    menit_terlambat: Mapped[int] = mapped_column(Integer, default=0)
    terlambat: Mapped[bool] = mapped_column(Boolean, default=False)
    menit_pulang_cepat: Mapped[int] = mapped_column(Integer, default=0)
    durasi_kerja: Mapped[float | None] = mapped_column(Float, nullable=True)  # jam
    menit_lembur: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint("nik", "tanggal", name="uq_attendance_nik_tanggal"),
        Index("ix_attendance_tanggal_dept", "tanggal", "departemen"),
        Index("ix_attendance_nik", "nik"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    detail: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ========== Kinerja Penjualan (Rilis E) ==========

class TargetLevel(str, enum.Enum):
    """Tiga tingkat target penjualan: Perusahaan, Cabang, Salesman (PRD F33)"""
    PERUSAHAAN = "Perusahaan"
    CABANG = "Cabang"
    SALESMAN = "Salesman"


class AssessmentStatus(str, enum.Enum):
    """Status Persetujuan Penilaian (PRD F32)"""
    DRAFT = "Draf"
    APPROVED = "Disetujui"
    REJECTED = "Ditolak"


class EligibilityRecommendation(str, enum.Enum):
    """Rekomendasi kelayakan performance dari sistem (PRD F34)"""
    ELIGIBLE = "Layak"
    NOT_ELIGIBLE = "Tidak Layak"
    NO_TARGET = "Target belum ada"


class SalesPerformance(Base):
    """Data penjualan salesman per bulan dari sheet Penjualan (PRD bagian 13)"""
    __tablename__ = "sales_performance"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[str] = mapped_column(String(30))  # NIK salesman
    nama: Mapped[str] = mapped_column(String(150))
    cabang: Mapped[str] = mapped_column(String(100))
    periode: Mapped[str] = mapped_column(String(7))  # "2026-09" format YYYY-MM
    penjualan: Mapped[float] = mapped_column(Float)
    penerimaan: Mapped[float | None] = mapped_column(Float, nullable=True)
    piutang_outlet: Mapped[float | None] = mapped_column(Float, nullable=True)
    sisa_bad_debt: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint("employee_id", "periode", name="uq_sales_performance_emp_periode"),
        Index("ix_sales_performance_cabang_periode", "cabang", "periode"),
    )


class SalesTarget(Base):
    """Target penjualan 3 tingkat per bulan: Perusahaan, Cabang, Salesman (PRD F33)"""
    __tablename__ = "sales_targets"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), index=True)
    level: Mapped[TargetLevel] = mapped_column(Enum(TargetLevel, native_enum=False, length=20))
    cabang: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Null untuk level Perusahaan
    employee_id: Mapped[str | None] = mapped_column(String(30), nullable=True)  # Hanya untuk level Salesman
    periode: Mapped[str] = mapped_column(String(7))  # "2026-09"
    target: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint("level", "cabang", "employee_id", "periode", name="uq_sales_target_level_periode"),
        Index("ix_sales_target_periode", "periode"),
    )


class SalesAssessment(Base):
    """Persetujuan Penilaian per salesman per bulan (PRD F32, F34)"""
    __tablename__ = "sales_assessments"
    
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[str] = mapped_column(String(30))
    periode: Mapped[str] = mapped_column(String(7))
    # Rekomendasi sistem berdasarkan target (F34)
    recommendation: Mapped[EligibilityRecommendation] = mapped_column(
        Enum(EligibilityRecommendation, native_enum=False, length=20)
    )
    # Status persetujuan (F32)
    status: Mapped[AssessmentStatus] = mapped_column(
        Enum(AssessmentStatus, native_enum=False, length=20),
        default=AssessmentStatus.DRAFT
    )
    approved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # Catatan penyetuju
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint("employee_id", "periode", name="uq_sales_assessment_emp_periode"),
        Index("ix_sales_assessment_status", "status"),
    )
