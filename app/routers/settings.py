from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from fastapi import APIRouter, Depends, HTTPException, Query, Request

import polars as pl

from app.db import get_db
from app.deps import HR, get_current_user
from app.models import Attendance as A, Holiday, ShiftRule, User
from app.schemas import HolidayIn, HolidayOut, RecalcRequest, RecalcResponse, ShiftIn, ShiftOut
from app.services import audit
from app.services.calculator import compute
from app.services.rules import DEFAULT_SHIFT, hhmm_to_min, holidays_list, min_to_hhmm, rules_frame

router = APIRouter(prefix="/settings", tags=["pengaturan"], dependencies=[HR])


def _shift_out(r: ShiftRule) -> ShiftOut:
    return ShiftOut(
        kode=r.kode, jam_masuk=min_to_hhmm(r.jam_masuk_menit), jam_pulang=min_to_hhmm(r.jam_pulang_menit),
        toleransi_menit=r.toleransi_menit, lintas_hari=r.jam_pulang_menit <= r.jam_masuk_menit,
    )


@router.get("/shifts", response_model=list[ShiftOut])
def list_shifts(db: Session = Depends(get_db)):
    return [_shift_out(r) for r in db.scalars(select(ShiftRule).order_by(ShiftRule.kode))]


@router.put("/shifts/{kode}", response_model=ShiftOut)
def upsert_shift(kode: str, body: ShiftIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    kode = kode.strip().upper()
    if not kode or len(kode) > 20:
        raise HTTPException(422, "Kode shift harus 1 sampai 20 karakter")
    try:
        start, end = hhmm_to_min(body.jam_masuk), hhmm_to_min(body.jam_pulang)
    except ValueError:
        raise HTTPException(422, "Format jam harus HH:MM")
    if start == end:
        raise HTTPException(422, "Jam masuk dan jam pulang tidak boleh sama")
    row = db.get(ShiftRule, kode) or ShiftRule(kode=kode)
    row.jam_masuk_menit, row.jam_pulang_menit, row.toleransi_menit = start, end, body.toleransi_menit
    db.add(row)
    audit.log(db, user.id, "shift_upsert", {"kode": kode, **body.model_dump()}, request.client.host if request.client else None)
    db.commit()
    return _shift_out(row)


@router.delete("/shifts/{kode}", status_code=204)
def delete_shift(kode: str, db: Session = Depends(get_db)):
    kode = kode.strip().upper()
    if kode == DEFAULT_SHIFT:
        raise HTTPException(409, "Shift REGULER tidak dapat dihapus")
    row = db.get(ShiftRule, kode)
    if row is None:
        raise HTTPException(404, "Shift tidak ditemukan")
    if db.scalar(select(func.count(A.id)).where(A.shift == kode)):
        raise HTTPException(409, "Shift masih dipakai oleh data presensi")
    db.delete(row)
    db.commit()


@router.get("/holidays", response_model=list[HolidayOut])
def list_holidays(year: int | None = Query(None, ge=2000, le=2100), db: Session = Depends(get_db)):
    stmt = select(Holiday).order_by(Holiday.tanggal)
    if year:
        stmt = stmt.where(Holiday.tanggal >= date(year, 1, 1), Holiday.tanggal <= date(year, 12, 31))
    return list(db.scalars(stmt))


@router.post("/holidays", response_model=HolidayOut, status_code=201)
def add_holiday(body: HolidayIn, db: Session = Depends(get_db)):
    if db.scalar(select(Holiday).where(Holiday.tanggal == body.tanggal)):
        raise HTTPException(409, "Tanggal ini sudah ada di daftar hari libur")
    row = Holiday(tanggal=body.tanggal, nama=body.nama.strip())
    db.add(row)
    db.commit()
    return row


@router.delete("/holidays/{holiday_id}", status_code=204)
def delete_holiday(holiday_id: int, db: Session = Depends(get_db)):
    row = db.get(Holiday, holiday_id)
    if row is None:
        raise HTTPException(404, "Hari libur tidak ditemukan")
    db.delete(row)
    db.commit()


@router.post("/recalculate", response_model=RecalcResponse)
def recalculate(body: RecalcRequest, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Hitung ulang status, keterlambatan, durasi, dan lembur untuk satu periode memakai aturan terbaru."""
    if body.date_from > body.date_to:
        raise HTTPException(422, "Tanggal awal tidak boleh setelah tanggal akhir")
    rows = db.execute(
        select(A.id, A.nik, A.nama, A.tanggal, A.departemen, A.shift, A.jam_masuk_menit, A.jam_keluar_menit, A.status_file, A.keterangan, A.lokasi_kerja)
        .where(A.tanggal >= body.date_from, A.tanggal <= body.date_to)
    ).all()
    if not rows:
        return RecalcResponse(updated=0)
    df = pl.DataFrame(
        [tuple(r) for r in rows],
        schema={"id": pl.Int64, "nik": pl.Utf8, "nama": pl.Utf8, "tanggal": pl.Date, "departemen": pl.Utf8, "shift": pl.Utf8,
                "jam_masuk_menit": pl.Int64, "jam_keluar_menit": pl.Int64, "status_in": pl.Utf8, "keterangan": pl.Utf8, "lokasi_kerja": pl.Utf8},
        orient="row",
    )
    out = compute(df.drop("id"), rules_frame(db), holidays_list(db)).with_columns(id=df["id"])
    fields = ["status", "hari_kerja", "menit_terlambat", "terlambat", "menit_pulang_cepat", "durasi_kerja", "menit_lembur"]
    payload = out.select(["id", *fields]).to_dicts()
    for i in range(0, len(payload), 5000):
        db.execute(update(A), payload[i : i + 5000])
    audit.log(db, user.id, "recalculate", {"from": str(body.date_from), "to": str(body.date_to), "rows": len(payload)},
              request.client.host if request.client else None)
    db.commit()
    return RecalcResponse(updated=len(payload))
