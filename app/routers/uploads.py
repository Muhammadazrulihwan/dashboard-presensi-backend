import io
import uuid
from datetime import timezone
from pathlib import Path

import xlsxwriter
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.deps import HR_ONLY, get_current_user
from app.models import Attendance, Upload, UploadStatus, User
from app.schemas import ConfirmRequest, ConfirmResponse, PreviewResponse, UploadOut
from app.services import audit
from app.services.calculator import compute
from app.services.cleanup import discard_file
from app.services.file_reader import read_table
from app.services.importer import find_conflicts, import_rows
from app.services.rules import holidays_list, rules_frame, rules_map
from app.services.validator import validate

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[HR_ONLY])
ALLOWED = {".csv", ".xlsx"}


@router.get("/template")
def download_template():
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Presensi")
    head = wb.add_format({"bold": True, "bg_color": "#E1EFEC", "border": 1})
    text = wb.add_format({"num_format": "@"})
    cols = ["nik", "nama", "tanggal", "departemen", "shift", "jam_masuk", "jam_keluar", "status", "keterangan", "lokasi_kerja"]
    for i, name in enumerate(cols):
        ws.write(0, i, name, head)
    ws.set_column(0, 0, 14, text)  # nik sebagai teks agar angka nol di depan tidak hilang
    ws.set_column(2, 2, 13, text)
    ws.set_column(5, 6, 11, text)
    ws.set_column(1, 1, 24)
    ws.set_column(3, 4, 14)
    ws.set_column(7, 9, 16)
    samples = [
        ["0012345", "Contoh Karyawan", "2026-10-05", "IT", "", "07:58", "17:05", "Hadir", "", "WFO"],
        ["0012345", "Contoh Karyawan", "2026-10-06", "IT", "", "", "", "Sakit", "Demam", "WFH"],
    ]
    for r, row in enumerate(samples, start=1):
        for c, v in enumerate(row):
            ws.write_string(r, c, v)
    info = wb.add_worksheet("Petunjuk")
    info.set_column(0, 0, 110)
    for i, line in enumerate([
        "Satu baris mewakili satu karyawan pada satu tanggal.",
        "Kolom wajib: nik, nama, tanggal (YYYY-MM-DD), jam_masuk (HH:MM), jam_keluar (HH:MM), status.",
        "Status: Hadir, Izin, Sakit, Cuti, Dinas Luar, Alpa. Jika kosong dan tidak ada jam masuk pada hari kerja, dihitung Alpa.",
        "Jam masuk dan jam keluar wajib untuk status Hadir. Keterangan wajib untuk Izin, Sakit, dan Cuti.",
        "departemen disarankan diisi. shift boleh kosong (dianggap REGULER). Kode shift harus sudah diatur di menu Pengaturan.",
        "Kolom keterlambatan, durasi, dan lembur dihitung otomatis oleh sistem, tidak perlu diisi.",
    ]):
        info.write(i, 0, line)
    wb.close()
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="template_presensi.xlsx"'},
    )


def _save_stream(file: UploadFile, dest: Path) -> int:
    """Tulis file ke disk per potongan 1 MB, berhenti jika melebihi batas ukuran."""
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with open(dest, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"Ukuran file melebihi {settings.max_upload_mb} MB")
            out.write(chunk)
    return size


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/preview", response_model=PreviewResponse)
def preview(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    original = Path(file.filename or "upload").name
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "Format file tidak didukung. Gunakan .csv atau .xlsx")

    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.storage_dir / f"{uuid.uuid4().hex}{ext}"
    _save_stream(file, dest)

    up = Upload(filename=original, stored_path=str(dest), uploaded_by=user.id, status=UploadStatus.pending)
    db.add(up)
    db.commit()

    try:
        df, mapped = read_table(dest)
        result = validate(df, rules_map(db))
    except Exception:
        up.status, up.row_count = UploadStatus.failed, 0
        up.error_summary = {"message": "File tidak dapat dibaca"}
        db.commit()
        discard_file(up.stored_path)
        raise HTTPException(422, "File tidak dapat dibaca. Pastikan file CSV atau XLSX valid dan tidak rusak")

    conflicts, date_from, date_to, employees = 0, None, None, 0
    if result.error_count:
        up.status, up.row_count = UploadStatus.failed, 0
        up.error_summary = {"count": result.error_count, "errors": result.errors[:50]}
    else:
        up.status, up.row_count = UploadStatus.validated, result.row_count
        clean = result.clean
        conflicts = find_conflicts(db, clean).height
        date_from, date_to, employees = clean["tanggal"].min(), clean["tanggal"].max(), clean["nik"].n_unique()
    audit.log(db, user.id, "upload_preview", {"upload_id": up.id, "filename": original, "errors": result.error_count}, _ip(request))
    db.commit()
    if result.error_count:
        discard_file(up.stored_path)  # file gagal tidak akan dipakai lagi

    return PreviewResponse(
        upload_id=up.id,
        filename=original,
        row_count=result.row_count,
        preview_rows=result.preview,
        errors=result.errors,
        error_count=result.error_count,
        can_save=result.error_count == 0,
        conflict_count=conflicts,
        date_from=date_from,
        date_to=date_to,
        employee_count=employees,
        mapped_columns=mapped,
    )


def _out(up: Upload, uploader: str) -> UploadOut:
    created = up.created_at
    if created.tzinfo is None:  # SQLite menyimpan UTC tanpa zona waktu
        created = created.replace(tzinfo=timezone.utc)
    return UploadOut(id=up.id, filename=up.filename, uploader=uploader, created_at=created, row_count=up.row_count, status=up.status.value)


@router.post("/{upload_id}/confirm", response_model=ConfirmResponse)
def confirm(
    upload_id: int,
    request: Request,
    body: ConfirmRequest | None = Body(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    up = db.get(Upload, upload_id)
    if up is None:
        raise HTTPException(404, "Upload tidak ditemukan")
    if up.status != UploadStatus.validated:
        raise HTTPException(409, "Upload ini sudah disimpan atau tidak lolos validasi")
    try:
        df, _ = read_table(Path(up.stored_path))
    except Exception:
        raise HTTPException(410, "File sementara sudah tidak tersedia. Unggah ulang file Anda")
    result = validate(df, rules_map(db))
    if result.error_count or result.clean is None:
        raise HTTPException(409, "File tidak lolos validasi")

    computed = compute(result.clean, rules_frame(db), holidays_list(db))
    conflicts = find_conflicts(db, computed)
    mode = body.mode if body else None
    if conflicts.height and mode is None:
        raise HTTPException(409, f"Ada {conflicts.height} data bentrok (nik dan tanggal sama). Pilih Timpa atau Lewati")
    try:
        stats = import_rows(db, up.id, computed, mode, conflicts)
        up.status, up.row_count = UploadStatus.saved, stats["inserted"]
        audit.log(db, user.id, "upload_confirm", {"upload_id": up.id, "mode": mode, **stats}, _ip(request))
        db.commit()
    except Exception:
        db.rollback()
        up.status = UploadStatus.failed
        db.commit()
        raise HTTPException(500, "Gagal menyimpan data. Coba ulangi upload")
    return ConfirmResponse(upload=_out(up, user.full_name), **stats)


@router.get("", response_model=list[UploadOut])
def history(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    visible = [UploadStatus.saved, UploadStatus.failed, UploadStatus.deleted]
    if status:
        if status not in {s.value for s in visible}:
            raise HTTPException(400, "Status tidak valid")
        visible = [UploadStatus(status)]
    rows = db.execute(
        select(Upload, User.full_name)
        .join(User, User.id == Upload.uploaded_by)
        .where(Upload.status.in_(visible))
        .order_by(Upload.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    return [_out(up, name) for up, name in rows]


@router.delete("/{upload_id}", response_model=UploadOut)
def delete_upload(upload_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    up = db.get(Upload, upload_id)
    if up is None or up.status == UploadStatus.deleted:
        raise HTTPException(404, "Upload tidak ditemukan")
    db.execute(delete(Attendance).where(Attendance.upload_id == up.id))
    up.status = UploadStatus.deleted
    discard_file(up.stored_path)
    audit.log(db, user.id, "upload_delete", {"upload_id": up.id, "filename": up.filename}, _ip(request))
    db.commit()
    uploader = db.get(User, up.uploaded_by)
    return _out(up, uploader.full_name if uploader else "-")
