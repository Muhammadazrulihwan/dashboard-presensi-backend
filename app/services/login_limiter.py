"""Pembatas percobaan login berbasis database (PRD Bab 12).

Disimpan di tabel `login_attempts` agar berlaku juga bila backend dijalankan multi-worker
(sebelumnya di memori proses). Satu baris per (email, IP). Baris dihapus saat login berhasil
atau saat masa kunci sudah habis.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import LoginAttempt


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _get(db: Session, email: str, ip: str) -> LoginAttempt | None:
    return db.scalar(select(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.ip_address == ip))


def locked_seconds(db: Session, email: str, ip: str) -> int:
    """Sisa detik kunci bila (email, IP) sedang dikunci; 0 bila tidak.

    Baris yang masa kuncinya sudah habis dihapus agar hitungan mulai dari nol lagi.
    """
    row = _get(db, email, ip)
    if row is None or row.gagal < settings.login_max_attempts:
        return 0
    sampai = _aware(row.terkunci_sampai)
    if sampai is None:
        return 0
    left = (sampai - _now()).total_seconds()
    if left <= 0:
        db.delete(row)
        db.commit()
        return 0
    return int(left) + 1


def register_fail(db: Session, email: str, ip: str) -> None:
    """Tambah hitungan gagal dan perpanjang masa kunci."""
    row = _get(db, email, ip)
    if row is None:
        row = LoginAttempt(email=email, ip_address=ip, gagal=0)
        db.add(row)
    row.gagal += 1
    if row.gagal >= settings.login_max_attempts:
        row.terkunci_sampai = _now() + timedelta(minutes=settings.login_lock_minutes)
    db.commit()


def clear(db: Session, email: str, ip: str) -> None:
    """Hapus hitungan setelah login berhasil."""
    db.execute(delete(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.ip_address == ip))
    db.commit()


def purge_expired(db: Session) -> int:
    """Bersihkan baris yang masa kuncinya sudah lewat (dipanggil saat startup)."""
    rows = db.scalars(select(LoginAttempt)).all()
    n = 0
    for row in rows:
        sampai = _aware(row.terkunci_sampai)
        if sampai is not None and sampai <= _now():
            db.delete(row)
            n += 1
    if n:
        db.commit()
    return n
