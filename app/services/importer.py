import polars as pl
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import (
    Attendance as A, AttendanceCorrection, Employee, RawActivity, RawReport, TimeOffRequest,
)

CHUNK = 5000


def find_conflicts(db: Session, df: pl.DataFrame) -> pl.DataFrame:
    """Baris di file yang nik dan tanggalnya sudah ada di database. Kolom: nik, tanggal, id (id data lama)."""
    schema = {"id": pl.Int64, "nik": pl.Utf8, "tanggal": pl.Date}
    if df.height == 0:
        return pl.DataFrame(schema=schema)
    rows = db.execute(select(A.id, A.nik, A.tanggal).where(A.tanggal >= df["tanggal"].min(), A.tanggal <= df["tanggal"].max())).all()
    existing = pl.DataFrame([tuple(r) for r in rows], schema=schema, orient="row")
    return df.select("nik", "tanggal").join(existing, on=["nik", "tanggal"], how="inner")


def import_rows(db: Session, upload_id: int, df: pl.DataFrame, mode: str | None, conflicts: pl.DataFrame) -> dict:
    """Simpan hasil hitung. Bentrok: mode 'overwrite' menghapus data lama, 'skip' melewati baris di file."""
    skipped = overwritten = 0
    if conflicts.height:
        if mode == "skip":
            df = df.join(conflicts.select("nik", "tanggal"), on=["nik", "tanggal"], how="anti")
            skipped = conflicts.height
        else:
            ids = conflicts["id"].to_list()
            for i in range(0, len(ids), 500):
                db.execute(delete(A).where(A.id.in_(ids[i : i + 500])))
            overwritten = len(ids)
    for part in df.iter_slices(CHUNK):
        db.execute(insert(A), [{"upload_id": upload_id, **r} for r in part.to_dicts()])
    return {"inserted": df.height, "overwritten": overwritten, "skipped": skipped}


# ---------- Paket empat sumber (Rilis B) ----------

def clear_sources(db: Session, tanggal_awal, tanggal_akhir) -> None:
    """Hapus data mentah empat sumber pada rentang tanggal (diganti oleh paket baru)."""
    db.execute(delete(RawActivity).where(RawActivity.tanggal >= tanggal_awal, RawActivity.tanggal <= tanggal_akhir))
    db.execute(delete(RawReport).where(RawReport.tanggal >= tanggal_awal, RawReport.tanggal <= tanggal_akhir))
    db.execute(delete(TimeOffRequest).where(
        TimeOffRequest.tanggal_mulai <= tanggal_akhir,
        (TimeOffRequest.tanggal_selesai.is_(None)) | (TimeOffRequest.tanggal_selesai >= tanggal_awal),
    ))
    db.execute(delete(AttendanceCorrection).where(
        AttendanceCorrection.tanggal_efektif >= tanggal_awal, AttendanceCorrection.tanggal_efektif <= tanggal_akhir,
    ))


def save_sources(db: Session, upload_id: int, parsed: dict) -> dict:
    """Simpan baris mentah tiap sumber. `parsed` = {"activity": Parsed, ...}."""
    counts = {}
    act = parsed.get("activity")
    if act:
        for r in act.rows:
            db.add(RawActivity(upload_id=upload_id, **r))
        counts["activity"] = len(act.rows)
    rep = parsed.get("report")
    if rep:
        for r in rep.rows:
            db.add(RawReport(upload_id=upload_id, **r))
        counts["report"] = len(rep.rows)
    tio = parsed.get("timeoff")
    if tio:
        for r in tio.rows:
            db.add(TimeOffRequest(upload_id=upload_id, **r))
        counts["timeoff"] = len(tio.rows)
    app = parsed.get("approval")
    if app:
        for r in app.rows:
            db.add(AttendanceCorrection(upload_id=upload_id, **r))
        counts["approval"] = len(app.rows)
    return counts


def upsert_employees(db: Session, rows: list[dict]) -> int:
    """Simpan/perbarui master karyawan (PRD F15)."""
    n = 0
    for r in rows:
        row = db.get(Employee, r["employee_id"])
        if row is None:
            db.add(Employee(**r))
        else:
            for k, v in r.items():
                setattr(row, k, v)
        n += 1
    return n


def save_rekap(db: Session, upload_id: int, rows: list[dict], mode: str | None) -> dict:
    """Simpan Rekap Absensi terpadu. mode overwrite/skip untuk bentrok nik+tanggal."""
    skipped = overwritten = 0
    if not rows:
        return {"inserted": 0, "overwritten": 0, "skipped": 0}
    niks = sorted({r["nik"] for r in rows})
    tgls = [r["tanggal"] for r in rows]
    lo, hi = min(tgls), max(tgls)
    existing = db.execute(
        select(A.id, A.nik, A.tanggal).where(A.tanggal >= lo, A.tanggal <= hi, A.nik.in_(niks))
    ).all()
    conflict_ids = {(r.nik, r.tanggal): r.id for r in existing}

    if conflict_ids and mode == "skip":
        kept = [r for r in rows if (r["nik"], r["tanggal"]) not in conflict_ids]
        skipped = len(rows) - len(kept)
        rows = kept
    elif conflict_ids:
        ids = list(conflict_ids.values())
        for i in range(0, len(ids), 500):
            db.execute(delete(A).where(A.id.in_(ids[i : i + 500])))
        overwritten = len(ids)

    for i in range(0, len(rows), CHUNK):
        db.execute(insert(A), [{"upload_id": upload_id, **r} for r in rows[i : i + CHUNK]])
    return {"inserted": len(rows), "overwritten": overwritten, "skipped": skipped}

