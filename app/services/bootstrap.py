import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import DEV_JWT_SECRET, settings
from app.core.security import hash_password
from app.models import Department, Departments, User

log = logging.getLogger("presensi.bootstrap")


def check_production_settings() -> None:
    """Hentikan startup produksi bila konfigurasi keamanan masih bawaan pengembangan."""
    if not settings.is_production:
        return
    problems = []
    if settings.jwt_secret == DEV_JWT_SECRET or len(settings.jwt_secret) < 32:
        problems.append("JWT_SECRET harus diisi acak minimal 32 karakter")
    if not settings.cookie_secure:
        problems.append("COOKIE_SECURE harus true (HTTPS)")
    if problems:
        raise RuntimeError("Konfigurasi produksi tidak aman: " + "; ".join(problems))


def ensure_departments(db: Session) -> None:
    """Isi tabel departemen baku bila belum ada. Nilai baku agar pemeriksaan hak akses tidak gagal karena salah ketik."""
    existing = set(db.scalars(select(Departments.kode)))
    added = False
    for d in Department:
        if d.value not in existing:
            db.add(Departments(kode=d.value, nama=d.value))
            added = True
    if added:
        db.commit()


def ensure_admin(db: Session) -> None:
    """Buat akun admin IT pertama dari ADMIN_EMAIL dan ADMIN_PASSWORD bila belum ada.

    - Password akun yang sudah ada tidak pernah ditimpa, jadi aman dijalankan tiap startup.
    - User dummy lama (id 1, password_hash "!") diubah menjadi admin ini agar riwayat upload tetap terhubung.
    """
    email = (settings.admin_email or "").strip().lower()
    if not email or not settings.admin_password:
        if db.scalar(select(func.count()).select_from(User).where(
                User.departemen == Department.IT.value, User.password_hash != "!")) == 0:
            log.warning("Belum ada akun admin IT. Isi ADMIN_EMAIL dan ADMIN_PASSWORD di .env lalu jalankan ulang backend.")
        return
    if len(settings.admin_password) < 8:
        raise RuntimeError("ADMIN_PASSWORD minimal 8 karakter")

    if db.scalar(select(User).where(func.lower(User.email) == email)) is not None:
        return
    legacy = db.get(User, 1)
    if legacy is not None and legacy.password_hash == "!":
        legacy.email, legacy.full_name = email, settings.admin_name
        legacy.password_hash, legacy.departemen, legacy.is_active = hash_password(settings.admin_password), Department.IT.value, True
    else:
        db.add(User(email=email, full_name=settings.admin_name,
                    password_hash=hash_password(settings.admin_password), departemen=Department.IT.value))
    db.commit()
    log.info("Akun admin IT %s dibuat", email)
