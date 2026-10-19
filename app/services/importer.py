import polars as pl
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.models import Attendance as A

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
