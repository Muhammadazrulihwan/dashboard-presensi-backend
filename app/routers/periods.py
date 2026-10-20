"""Tutup dan buka periode (PRD F13).

Hanya HR. Menutup periode mengunci data bulan itu dari upload, hitung ulang, dan perubahan lain.
Membuka kembali wajib dengan alasan. Semua aksi tercatat di audit log.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import HR, get_current_user
from app.models import Attendance, Period, User
from app.schemas import PeriodCloseRequest, PeriodOut, PeriodReopenRequest
from app.services import audit
from app.services import period as svc

router = APIRouter(prefix="/periods", tags=["periode"], dependencies=[HR])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _out(db: Session, row: Period) -> PeriodOut:
    records = db.scalar(
        select(func.count(Attendance.id)).where(Attendance.periode_ym == f"{row.tahun:04d}-{row.bulan:02d}")
    ) or 0
    user = db.get(User, row.ditutup_oleh) if row.ditutup_oleh else None
    return PeriodOut(
        tahun=row.tahun, bulan=row.bulan, periode=f"{row.tahun:04d}-{row.bulan:02d}",
        ditutup=row.ditutup, ditutup_oleh=user.full_name if user else None,
        ditutup_at=row.ditutup_at, alasan=row.alasan, records=int(records),
    )


@router.get("", response_model=list[PeriodOut])
def list_periods(tahun: int | None = Query(None, ge=2000, le=2100), db: Session = Depends(get_db)):
    """Daftar periode yang pernah ditutup/dibuka, plus baris rekap per bulan."""
    rows = svc.list_periods(db, tahun)
    return [_out(db, r) for r in rows]


@router.post("/close", response_model=PeriodOut)
def close_period(body: PeriodCloseRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if svc.is_closed(db, body.tahun, body.bulan):
        raise HTTPException(409, "Periode ini sudah ditutup")
    row = svc.close(db, body.tahun, body.bulan, user.id, body.alasan)
    audit.log(db, user.id, "period_close",
              {"periode": f"{body.tahun:04d}-{body.bulan:02d}", "alasan": body.alasan}, _ip(request))
    db.commit()
    return _out(db, row)


@router.post("/reopen", response_model=PeriodOut)
def reopen_period(body: PeriodReopenRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not svc.is_closed(db, body.tahun, body.bulan):
        raise HTTPException(409, "Periode ini belum ditutup")
    row = svc.reopen(db, body.tahun, body.bulan, user.id, body.alasan)
    audit.log(db, user.id, "period_reopen",
              {"periode": f"{body.tahun:04d}-{body.bulan:02d}", "alasan": body.alasan}, _ip(request))
    db.commit()
    return _out(db, row)
