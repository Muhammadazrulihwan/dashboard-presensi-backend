from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import burn_time, create_token, verify_password
from app.db import SessionLocal, get_db
from app.deps import get_current_user
from app.models import LoginAttempt, User
from app.services import audit
from app.services import login_limiter

router = APIRouter(prefix="/auth", tags=["auth"])

BAD_CREDENTIALS = "Email atau password salah"


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class MeOut(BaseModel):
    id: int
    email: str
    full_name: str
    departemen: str | None = None
    is_approver: bool = False


def _me(u: User) -> MeOut:
    return MeOut(id=u.id, email=u.email, full_name=u.full_name, departemen=u.departemen, is_approver=u.is_approver)


# Pembatas percobaan login disimpan di tabel `login_attempts` (lihat services/login_limiter.py),
# bukan di memori proses, agar berlaku juga saat backend dijalankan multi-worker (PRD Bab 12).


def reset_rate_limit() -> None:
    """Dipakai tes: bersihkan seluruh hitungan percobaan login."""
    with SessionLocal() as db:
        db.execute(delete(LoginAttempt))
        db.commit()


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.cookie_name, token,
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True, secure=settings.cookie_secure, samesite=settings.cookie_samesite, path="/",
    )


@router.post("/login", response_model=MeOut)
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    ip = request.client.host if request.client else "-"

    wait = login_limiter.locked_seconds(db, email, ip)
    if wait:
        audit.log(db, None, "login_blocked", {"email": email}, ip)
        db.commit()
        raise HTTPException(429, f"Terlalu banyak percobaan gagal. Coba lagi dalam {-(-wait // 60)} menit.", headers={"Retry-After": str(wait)})

    user = db.scalar(select(User).where(func.lower(User.email) == email))
    ok = False
    if user is not None:
        ok = verify_password(body.password, user.password_hash) and user.is_active
    else:
        burn_time()

    if not ok:
        login_limiter.register_fail(db, email, ip)
        audit.log(db, user.id if user else None, "login_failed", {"email": email}, ip)
        db.commit()
        raise HTTPException(401, BAD_CREDENTIALS)

    login_limiter.clear(db, email, ip)
    audit.log(db, user.id, "login", None, ip)
    db.commit()
    _set_cookie(response, create_token(user.id))
    return _me(user)


@router.post("/logout", status_code=204)
def logout(response: Response):
    # Tanpa syarat login: logout pada sesi yang sudah habis tetap harus berhasil.
    response.delete_cookie(settings.cookie_name, path="/", httponly=True, secure=settings.cookie_secure, samesite=settings.cookie_samesite)
    return None


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return _me(user)
