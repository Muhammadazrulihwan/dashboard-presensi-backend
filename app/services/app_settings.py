"""Pengaturan aplikasi (key-value) yang bisa diubah HR, termasuk pembagi hari kerja payroll (PRD F29)."""
from sqlalchemy.orm import Session

from app.models import AppSetting

PAYROLL_WORKDAYS = "payroll_workdays"
DEFAULT_PAYROLL_WORKDAYS = 25
MIN_WORKDAYS, MAX_WORKDAYS = 1, 31


def get_int(db: Session, kunci: str, default: int) -> int:
    row = db.get(AppSetting, kunci)
    if row is None:
        return default
    try:
        return int(row.nilai)
    except (TypeError, ValueError):
        return default


def set_int(db: Session, kunci: str, nilai: int) -> None:
    row = db.get(AppSetting, kunci)
    if row is None:
        db.add(AppSetting(kunci=kunci, nilai=str(nilai)))
    else:
        row.nilai = str(nilai)


def payroll_workdays(db: Session) -> int:
    """Pembagi hari kerja untuk Rekap Payroll (default 25)."""
    n = get_int(db, PAYROLL_WORKDAYS, DEFAULT_PAYROLL_WORKDAYS)
    return n if MIN_WORKDAYS <= n <= MAX_WORKDAYS else DEFAULT_PAYROLL_WORKDAYS


def ensure_defaults(db: Session) -> None:
    """Pastikan baris pengaturan default ada (dipanggil saat startup)."""
    if db.get(AppSetting, PAYROLL_WORKDAYS) is None:
        set_int(db, PAYROLL_WORKDAYS, DEFAULT_PAYROLL_WORKDAYS)
        db.commit()
