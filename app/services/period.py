"""Tutup dan buka periode (PRD F13).

Satu baris `periods` per (tahun, bulan). Periode tertutup mengunci data bulan itu: upload,
hitung ulang, dan perubahan presensi ditolak sampai HR membukanya kembali dengan alasan.
Semua aksi tercatat di audit log oleh pemanggil.
"""
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Period


def _ym(d: date) -> tuple[int, int]:
    return d.year, d.month


def get_period(db: Session, tahun: int, bulan: int) -> Period | None:
    return db.scalar(select(Period).where(Period.tahun == tahun, Period.bulan == bulan))


def is_closed(db: Session, tahun: int, bulan: int) -> bool:
    row = get_period(db, tahun, bulan)
    return bool(row and row.ditutup)


def closed_months_in_range(db: Session, d1: date, d2: date) -> list[str]:
    """Daftar bulan "YYYY-MM" yang tertutup dan beririsan dengan rentang tanggal."""
    rows = db.scalars(
        select(Period).where(Period.ditutup.is_(True), Period.tahun * 100 + Period.bulan >= d1.year * 100 + d1.month,
                             Period.tahun * 100 + Period.bulan <= d2.year * 100 + d2.month)
    ).all()
    return [f"{r.tahun:04d}-{r.bulan:02d}" for r in rows]


def ensure_open(db: Session, d1: date, d2: date) -> None:
    """Lemparkan ValueError berisi daftar bulan tertutup bila rentang menyentuh periode terkunci."""
    closed = closed_months_in_range(db, d1, d2)
    if closed:
        raise ValueError(
            "Periode berikut sudah ditutup dan tidak dapat diubah: " + ", ".join(closed)
            + ". Buka kembali periode di menu Tutup Periode bila memang perlu diubah."
        )


def list_periods(db: Session, tahun: int | None = None) -> list[Period]:
    stmt = select(Period).order_by(Period.tahun.desc(), Period.bulan.desc())
    if tahun:
        stmt = stmt.where(Period.tahun == tahun)
    return list(db.scalars(stmt))


def close(db: Session, tahun: int, bulan: int, user_id: int, alasan: str | None) -> Period:
    row = get_period(db, tahun, bulan)
    if row is None:
        row = Period(tahun=tahun, bulan=bulan)
        db.add(row)
    row.ditutup = True
    row.ditutup_oleh = user_id
    row.ditutup_at = datetime.now(timezone.utc)
    row.alasan = (alasan or "").strip() or None
    return row


def reopen(db: Session, tahun: int, bulan: int, user_id: int, alasan: str) -> Period:
    row = get_period(db, tahun, bulan)
    if row is None:
        row = Period(tahun=tahun, bulan=bulan)
        db.add(row)
    row.ditutup = False
    row.ditutup_oleh = user_id
    row.ditutup_at = datetime.now(timezone.utc)
    row.alasan = alasan.strip()
    return row
