from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import require_department
from app.models import AuditLog, Department, User

# Audit log: HR, IT, dan Audit boleh melihat.
AUDIT_LOG_DEPTS = (Department.HR, Department.IT, Department.AUDIT)

router = APIRouter(prefix="/admin/audit-log", tags=["audit"], dependencies=[Depends(require_department(*AUDIT_LOG_DEPTS))])


class AuditLogItem(BaseModel):
    id: int
    user_id: int | None
    user_name: str | None
    user_email: str | None
    action: str
    detail: dict | None
    ip_address: str | None
    created_at: str


class AuditLogResponse(BaseModel):
    items: list[AuditLogItem]
    total: int
    page: int
    page_size: int


class AuditLogUser(BaseModel):
    id: int
    full_name: str
    email: str


# Label harus memakai action yang benar-benar ditulis lewat audit.log().
# Bila menambah audit.log baru di router, tambahkan labelnya di sini juga.
ACTION_LABELS = {
    # Autentikasi
    "login": "Login",
    "login_failed": "Login Gagal",
    "login_blocked": "Login Diblokir",
    # Upload presensi (HR)
    "upload_preview": "Periksa File Presensi",
    "upload_confirm": "Simpan File Presensi",
    "upload_delete": "Hapus Upload Presensi",
    "package_preview": "Periksa Paket Empat Sumber",
    "package_confirm": "Simpan Paket Empat Sumber",
    # Kinerja penjualan (Sales/Marketing)
    "sales_preview": "Periksa File Penjualan",
    "sales_confirm": "Simpan File Penjualan",
    "sales_assessment_update": "Persetujuan Penilaian",
    # Export
    "export_excel": "Export Excel Rekap",
    "export_pdf": "Export PDF Rekap",
    "export_employee_pdf": "Export PDF Individual",
    "export_employee_excel": "Export Excel Individual",
    "export_payroll": "Export Rekap Payroll",
    # Kelola user (IT)
    "user_create": "Buat User",
    "user_update": "Ubah User",
    "user_password_reset": "Reset Password",
    "user_deactivate": "Nonaktifkan User",
    # Pengaturan (HR)
    "shift_upsert": "Simpan Shift",
    "recalculate": "Hitung Ulang Presensi",
    "payroll_settings_update": "Ubah Pembagi Hari Kerja Payroll",
    # Tutup periode (HR)
    "period_close": "Tutup Periode",
    "period_reopen": "Buka Kembali Periode",
    # Arsip file (HR dan Audit)
    "archive_download": "Unduh Arsip",
    "archive_trash": "Pindah Arsip ke Tempat Sampah",
    "archive_restore": "Pulihkan Arsip",
    "archive_purge": "Hapus Arsip Permanen",
}


@router.get("", response_model=AuditLogResponse)
def get_audit_logs(
    user_id: int | None = None,
    action: str | None = None,
    from_date: str | None = Query(None, description="YYYY-MM-DD"),
    to_date: str | None = Query(None, description="YYYY-MM-DD"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    query = select(AuditLog, User.full_name, User.email).outerjoin(User, AuditLog.user_id == User.id)

    if user_id is not None:
        query = query.where(AuditLog.user_id == user_id)
    if action:
        query = query.where(AuditLog.action == action)
    if from_date:
        query = query.where(func.date(AuditLog.created_at) >= from_date)
    if to_date:
        query = query.where(func.date(AuditLog.created_at) <= to_date)

    count_query = select(func.count()).select_from(AuditLog)
    if user_id is not None:
        count_query = count_query.where(AuditLog.user_id == user_id)
    if action:
        count_query = count_query.where(AuditLog.action == action)
    if from_date:
        count_query = count_query.where(func.date(AuditLog.created_at) >= from_date)
    if to_date:
        count_query = count_query.where(func.date(AuditLog.created_at) <= to_date)
    total = db.scalar(count_query) or 0

    query = query.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = db.execute(query).all()

    items = [
        AuditLogItem(
            id=log.id,
            user_id=log.user_id,
            user_name=user_name,
            user_email=user_email,
            action=log.action,
            detail=log.detail,
            ip_address=log.ip_address,
            created_at=log.created_at.isoformat(),
        )
        for log, user_name, user_email in rows
    ]

    return AuditLogResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/users", response_model=list[AuditLogUser])
def get_audit_users(db: Session = Depends(get_db)):
    """Daftar pengguna yang muncul di audit log, untuk pilihan filter.

    Dipisah dari /admin/users (khusus IT) agar HR dan Audit juga bisa memakai halaman ini.
    """
    rows = db.execute(
        select(User.id, User.full_name, User.email)
        .join(AuditLog, AuditLog.user_id == User.id)
        .distinct()
        .order_by(User.full_name)
    ).all()
    return [AuditLogUser(id=r.id, full_name=r.full_name, email=r.email) for r in rows]


@router.get("/actions")
def get_actions():
    return {"actions": [{"value": k, "label": v} for k, v in ACTION_LABELS.items()]}
