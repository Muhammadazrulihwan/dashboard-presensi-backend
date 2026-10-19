from datetime import date

import polars as pl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Holiday, ShiftRule

DEFAULT_SHIFT = "REGULER"
DEFAULT_START, DEFAULT_END = 8 * 60, 17 * 60


def hhmm_to_min(s: str) -> int:
    h, m = s.split(":")[:2]
    h, m = int(h), int(m)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Jam tidak valid")
    return h * 60 + m


def min_to_hhmm(m: int | float | None) -> str | None:
    if m is None:
        return None
    m = int(round(m))
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def ensure_default_shift(db: Session) -> None:
    if db.get(ShiftRule, DEFAULT_SHIFT) is None:
        db.add(ShiftRule(kode=DEFAULT_SHIFT, jam_masuk_menit=DEFAULT_START, jam_pulang_menit=DEFAULT_END, toleransi_menit=0))
        db.commit()


def _end_adjusted(start: int, end: int) -> int:
    """Shift yang selesai di hari berikutnya (mis. 22:00 sampai 06:00) memakai jam selesai + 24 jam."""
    return end if end > start else end + 1440


def rules_map(db: Session) -> dict[str, tuple[int, int]]:
    """kode -> (menit mulai, menit selesai yang sudah disesuaikan). Selesai >= 1440 berarti lintas hari."""
    return {r.kode: (r.jam_masuk_menit, _end_adjusted(r.jam_masuk_menit, r.jam_pulang_menit)) for r in db.scalars(select(ShiftRule))}


def rules_frame(db: Session) -> pl.DataFrame:
    rows = db.scalars(select(ShiftRule)).all()
    return pl.DataFrame(
        {
            "kode": [r.kode for r in rows],
            "start_min": [r.jam_masuk_menit for r in rows],
            "end_min": [_end_adjusted(r.jam_masuk_menit, r.jam_pulang_menit) for r in rows],
            "tol": [r.toleransi_menit for r in rows],
        },
        schema={"kode": pl.Utf8, "start_min": pl.Int64, "end_min": pl.Int64, "tol": pl.Int64},
    )


def holidays_list(db: Session) -> list[date]:
    return list(db.scalars(select(Holiday.tanggal)))
