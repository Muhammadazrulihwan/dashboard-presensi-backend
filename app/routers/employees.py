"""Master karyawan (PRD F15).

Data dibangun otomatis dari file upload (Report Attendance + Activity) dan disimpan di tabel `employees`.
Endpoint di sini hanya membaca: daftar karyawan dengan pencarian/filter/paginasi, detail satu karyawan,
dan daftar pilihan filter. Batas data mengikuti departemen akun (Sales/Marketing hanya melihat
karyawan Sales/Marketing), sama seperti dashboard.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import DASHBOARD, data_scope, get_current_user
from app.models import Employee, User
from app.schemas import EmployeeMeta, EmployeeOut, EmployeePage

router = APIRouter(prefix="/employees", tags=["karyawan"], dependencies=[DASHBOARD])


def _like(col, text: str):
    esc = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.lower(col).like(f"%{esc}%", escape="\\")


def _scope_conds(user: User) -> list:
    """Batas karyawan menurut departemen akun. Sales/Marketing hanya organisasi Sales/Marketing."""
    scope = data_scope(user)
    if scope is None:
        return []
    return [func.lower(Employee.organisasi).in_(scope)]


def _out(row: Employee) -> EmployeeOut:
    return EmployeeOut(
        employee_id=row.employee_id, nama=row.nama, jabatan=row.jabatan, organisasi=row.organisasi,
        cabang=row.cabang, status_kerja=row.status_kerja, resign=bool(row.resign), updated_at=row.updated_at,
    )


@router.get("/meta", response_model=EmployeeMeta)
def get_meta(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Pilihan filter dan ringkasan singkat pada master karyawan."""
    conds = _scope_conds(user)
    orgs = list(db.scalars(
        select(Employee.organisasi).where(*conds, Employee.organisasi.is_not(None)).distinct().order_by(Employee.organisasi)
    ))
    cabangs = list(db.scalars(
        select(Employee.cabang).where(*conds, Employee.cabang.is_not(None)).distinct().order_by(Employee.cabang)
    ))
    total = db.scalar(select(func.count()).select_from(Employee).where(*conds)) or 0
    resign = db.scalar(select(func.count()).select_from(Employee).where(*conds, Employee.resign.is_(True))) or 0
    return EmployeeMeta(organisasi=orgs, cabang=cabangs, resign_count=resign, total=total)


@router.get("", response_model=EmployeePage)
def list_employees(
    q: str | None = None,
    organisasi: str | None = None,
    cabang: str | None = None,
    resign: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Daftar karyawan. `q` mencocokkan Employee ID atau nama; filter organisasi/cabang/status resign."""
    conds = _scope_conds(user)
    if q and q.strip():
        text = q.strip()
        conds.append(or_(_like(Employee.employee_id, text), _like(Employee.nama, text)))
    if organisasi:
        conds.append(Employee.organisasi == organisasi)
    if cabang:
        conds.append(Employee.cabang == cabang)
    if resign is not None:
        conds.append(Employee.resign.is_(resign))

    total = db.scalar(select(func.count()).select_from(Employee).where(*conds)) or 0
    rows = db.scalars(
        select(Employee).where(*conds).order_by(Employee.nama, Employee.employee_id)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return EmployeePage(items=[_out(r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Detail satu karyawan. Di luar batas departemen akun diperlakukan seperti tidak ada (404)."""
    conds = _scope_conds(user)
    row = db.scalar(select(Employee).where(Employee.employee_id == employee_id, *conds))
    if row is None:
        raise HTTPException(404, "Karyawan tidak ditemukan")
    return _out(row)
