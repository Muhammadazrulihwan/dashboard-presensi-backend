from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db import get_db
from app.deps import get_current_user, require_department
from app.models import IT_DEPTS, Department, Departments, User
from app.schemas import PasswordReset, UserCreate, UserOut, UserUpdate
from app.services import audit

# Kelola user sepenuhnya milik departemen IT.
router = APIRouter(prefix="/admin/users", tags=["admin"], dependencies=[Depends(require_department(*IT_DEPTS))])

DEPT_VALUES = {d.value for d in Department}


def _out(u: User) -> UserOut:
    return UserOut(id=u.id, email=u.email, full_name=u.full_name, departemen=u.departemen,
                   is_approver=u.is_approver, is_active=u.is_active, created_at=u.created_at)


def _validated_dept(value: str | None) -> str | None:
    """Departemen wajib salah satu nilai baku bila diisi."""
    if value is None or not value.strip():
        return None
    v = value.strip()
    if v not in DEPT_VALUES:
        raise HTTPException(422, f"Departemen tidak valid. Pilihan: {', '.join(sorted(DEPT_VALUES))}")
    return v


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _active_it_count(db: Session, exclude_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(User).where(
        User.departemen == Department.IT.value, User.is_active.is_(True))
    if exclude_id is not None:
        stmt = stmt.where(User.id != exclude_id)
    return db.scalar(stmt) or 0


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db)):
    return [_out(u) for u in db.scalars(select(User).order_by(User.full_name))]


@router.get("/departments", response_model=list[str])
def list_departments(db: Session = Depends(get_db)):
    return list(db.scalars(select(Departments.kode).order_by(Departments.kode)))


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(409, "Email ini sudah terdaftar")
    dept = _validated_dept(body.departemen)
    row = User(email=email, full_name=body.full_name.strip(), password_hash=hash_password(body.password), departemen=dept)
    db.add(row)
    audit.log(db, user.id, "user_create", {"email": email, "departemen": dept}, _ip(request))
    db.commit()
    return _out(row)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(404, "User tidak ditemukan")
    if body.full_name is not None:
        row.full_name = body.full_name.strip()
    if body.departemen is not None:
        row.departemen = _validated_dept(body.departemen)
    if body.is_approver is not None:
        row.is_approver = body.is_approver
    # Hak penyetuju hanya berlaku untuk Sales/Marketing: ditolak bila diminta untuk departemen lain,
    # dan dicabut otomatis bila departemen akun berpindah dari Sales/Marketing.
    if row.is_approver and row.departemen != Department.SALES.value:
        if body.is_approver:
            raise HTTPException(422, "Hak penyetuju hanya untuk departemen Sales/Marketing")
        row.is_approver = False
    if body.is_active is not None:
        row.is_active = body.is_active

    # Cegah IT menonaktifkan atau menurunkan hak dirinya sendiri sebagai satu-satunya admin IT aktif.
    still_it = row.departemen == Department.IT.value and row.is_active
    if not still_it and _active_it_count(db, exclude_id=row.id) == 0:
        raise HTTPException(409, "Minimal satu akun departemen IT harus tetap aktif")

    audit.log(db, user.id, "user_update", {"user_id": row.id, **body.model_dump(exclude_none=True)}, _ip(request))
    db.commit()
    return _out(row)


@router.post("/{user_id}/password", status_code=204)
def reset_password(user_id: int, body: PasswordReset, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(404, "User tidak ditemukan")
    row.password_hash = hash_password(body.password)
    audit.log(db, user.id, "user_password_reset", {"user_id": row.id}, _ip(request))
    db.commit()
    return None


@router.delete("/{user_id}", status_code=204)
def deactivate_user(user_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Nonaktifkan user. Baris tidak dihapus agar riwayat upload tetap utuh."""
    row = db.get(User, user_id)
    if row is None:
        raise HTTPException(404, "User tidak ditemukan")
    if row.id == user.id:
        raise HTTPException(409, "Tidak dapat menonaktifkan akun Anda sendiri")
    if row.departemen == Department.IT.value and _active_it_count(db, exclude_id=row.id) == 0:
        raise HTTPException(409, "Minimal satu akun departemen IT harus tetap aktif")
    row.is_active = False
    audit.log(db, user.id, "user_deactivate", {"user_id": row.id, "email": row.email}, _ip(request))
    db.commit()
    return None
