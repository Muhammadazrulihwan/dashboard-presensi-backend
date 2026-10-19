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


ACTION_LABELS = {
    "login": "Login",
    "login_failed": "Login Gagal",
    "login_blocked": "Login Diblokir",
    "logout": "Logout",
    "upload": "Upload File",
    "upload_confirmed": "Upload Dikonfirmasi",
    "upload_deleted": "Upload Dihapus",
    "export_excel": "Export Excel",
    "export_pdf": "Export PDF",
    "export_employee_pdf": "Export PDF Karyawan",
    "export_employee_excel": "Export Excel Karyawan",
    "export_payroll": "Export Payroll",
    "user_created": "Buat User",
    "user_updated": "Update User",
    "user_password_reset": "Reset Password",
    "user_deactivated": "Nonaktifkan User",
    "shift_created": "Buat Shift",
    "shift_updated": "Update Shift",
    "shift_deleted": "Hapus Shift",
    "holiday_created": "Tambah Libur",
    "holiday_deleted": "Hapus Libur",
    "recalculate": "Hitung Ulang",
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


@router.get("/actions")
def get_actions():
    return {"actions": [{"value": k, "label": v} for k, v in ACTION_LABELS.items()]}
