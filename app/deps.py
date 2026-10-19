from dataclasses import replace

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decode_token
from app.db import get_db
from app.models import DASHBOARD_DEPTS, HR_DEPTS, IT_DEPTS, PERFORMANCE_DEPTS, Department, User
from app.services.aggregator import Filters, get_filters


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """User dari cookie sesi httpOnly. 401 bila belum login, token rusak/kedaluwarsa, atau akun nonaktif."""
    token = request.cookies.get(settings.cookie_name)
    user_id = decode_token(token) if token else None
    user = db.get(User, user_id) if user_id is not None else None
    if user is None or not user.is_active:
        raise HTTPException(401, "Sesi tidak valid atau sudah berakhir. Silakan login kembali.")
    return user


def require_department(*allowed: Department):
    """Batasi endpoint ke departemen tertentu. 403 bila departemen akun di luar daftar."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.departemen not in {d.value for d in allowed}:
            raise HTTPException(403, "Departemen Anda tidak memiliki akses untuk aksi ini")
        return user

    return checker


# Hak akses berbasis departemen (bukan peran).
DASHBOARD = Depends(require_department(*DASHBOARD_DEPTS))        # IT, HR, Audit, Sales/Marketing
PERFORMANCE = Depends(require_department(*PERFORMANCE_DEPTS))    # HR, Audit, Sales/Marketing
HR_ONLY = Depends(require_department(*HR_DEPTS))                 # upload presensi, pengaturan, tutup periode
HR = HR_ONLY
IT_ONLY = Depends(require_department(*IT_DEPTS))                 # kelola user
SALES_ONLY = Depends(require_department(Department.SALES))       # upload kinerja penjualan

# Alias lama agar router yang belum disentuh tetap jalan pada rilis ini.
ALL_ROLES = DASHBOARD
DETAIL_ROLES = PERFORMANCE


def data_scope(user: User) -> tuple[str, ...] | None:
    """Batas karyawan yang boleh dilihat akun ini. None = semua karyawan.

    PRD v0.4: Sales/Marketing hanya melihat karyawan Sales/Marketing. HR, Audit, dan IT melihat semua
    (usulan PRD, menunggu konfirmasi HR). Nama departemen pada data diatur lewat SALES_ORG_NAMES di .env.
    """
    if user.departemen == Department.SALES.value:
        return settings.sales_orgs
    return None


def scoped_filters(f: Filters = Depends(get_filters), user: User = Depends(get_current_user)) -> Filters:
    """Filter dashboard dan export, ditambah batas data menurut departemen akun.

    Batas dipasang di satu tempat (aggregator._where) sehingga dashboard, tabel, detail karyawan, dan semua export
    ikut terbatas. NIK di luar batas diperlakukan seperti tidak ada (404).
    """
    return replace(f, scope=data_scope(user))
