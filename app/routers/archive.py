"""Arsip file upload (PRD F22-F24).

- Cari arsip (F23): periode, jenis sumber, pengunggah, nama file.
- Unduh arsip: HR dan Audit (Audit lihat dan unduh).
- Hapus arsip (F24): HR memindahkan ke tempat sampah dengan alasan, lalu hapus permanen setelah
  masa tunggu habis. Semua aksi tercatat di audit log.
"""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.deps import HR_ONLY, get_current_user, require_department
from app.models import ArchiveStatus, ArchivedFile, ArchivedFileEmployee, Department, User
from app.schemas import ArchivedFileOut, ArchivedFilePage, TrashRequest
from app.services import archive as svc
from app.services import audit

# HR (hapus dan unduh) dan Audit (lihat dan unduh). IT dan Sales/Marketing ditolak.
VIEW_DEPTS = (Department.HR, Department.AUDIT)

router = APIRouter(prefix="/archive", tags=["arsip"], dependencies=[Depends(require_department(*VIEW_DEPTS))])


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _out(row: ArchivedFile, uploader: str | None, employee_ids: list[str] | None = None) -> ArchivedFileOut:
    return ArchivedFileOut(
        id=row.id,
        upload_id=row.upload_id,
        jenis_sumber=row.jenis_sumber,
        nama_asli=row.nama_asli,
        ukuran=row.ukuran,
        checksum=row.checksum,
        checksum_terverifikasi=svc.verify(row),
        pengunggah=uploader,
        periode_awal=row.periode_awal,
        periode_akhir=row.periode_akhir,
        status=row.status.value,
        created_at=row.created_at,
        dihapus_at=row.dihapus_at,
        alasan_hapus=row.alasan_hapus,
        bisa_dihapus_permanen=svc.can_purge(row),
        employee_ids=employee_ids or [],
    )


@router.get("", response_model=ArchivedFilePage)
def list_archive(
    status: str = Query("aktif", pattern="^(aktif|sampah|dihapus|semua)$"),
    jenis_sumber: str | None = None,
    pengunggah_id: int | None = None,
    q: str | None = None,
    employee_id: str | None = None,
    dari: date | None = None,
    sampai: date | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Cari arsip. `q` mencocokkan nama file; `employee_id` mencocokkan Employee ID yang tercatat di file;
    `dari`/`sampai` mencocokkan rentang tanggal data."""
    conds = []
    if status != "semua":
        conds.append(ArchivedFile.status == ArchiveStatus(status))
    if jenis_sumber:
        conds.append(ArchivedFile.jenis_sumber == jenis_sumber)
    if pengunggah_id is not None:
        conds.append(ArchivedFile.pengunggah_id == pengunggah_id)
    if q:
        esc = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds.append(func.lower(ArchivedFile.nama_asli).like(f"%{esc}%", escape="\\"))
    if employee_id and employee_id.strip():
        # Pencocokan presisi (bukan substring) lewat tabel anak, agar "LS01" tidak ikut mencocokkan "LS019".
        conds.append(
            ArchivedFile.id.in_(
                select(ArchivedFileEmployee.archive_id).where(
                    func.lower(ArchivedFileEmployee.employee_id) == employee_id.strip().lower()
                )
            )
        )
    if dari:
        conds.append(or_(ArchivedFile.periode_akhir.is_(None), ArchivedFile.periode_akhir >= dari))
    if sampai:
        conds.append(or_(ArchivedFile.periode_awal.is_(None), ArchivedFile.periode_awal <= sampai))

    total = db.scalar(select(func.count()).select_from(ArchivedFile).where(*conds)) or 0
    rows = db.scalars(
        select(ArchivedFile).where(*conds)
        .order_by(ArchivedFile.created_at.desc(), ArchivedFile.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    names = svc.uploader_names(db, {r.pengunggah_id for r in rows if r.pengunggah_id})
    emps = svc.employee_ids_map(db, [r.id for r in rows])
    return ArchivedFilePage(
        items=[_out(r, names.get(r.pengunggah_id), emps.get(r.id)) for r in rows],
        total=total, page=page, page_size=page_size,
    )


@router.get("/uploaders")
def list_uploaders(db: Session = Depends(get_db)):
    """Daftar pengunggah yang punya arsip, untuk pilihan filter."""
    rows = db.execute(
        select(User.id, User.full_name, User.email)
        .join(ArchivedFile, ArchivedFile.pengunggah_id == User.id)
        .distinct()
        .order_by(User.full_name)
    ).all()
    return [{"id": r.id, "full_name": r.full_name, "email": r.email} for r in rows]


def _get(db: Session, file_id: int) -> ArchivedFile:
    row = db.get(ArchivedFile, file_id)
    if row is None:
        raise HTTPException(404, "File arsip tidak ditemukan")
    return row


@router.get("/{file_id}/download")
def download(file_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = _get(db, file_id)
    if row.status == ArchiveStatus.dihapus:
        raise HTTPException(410, "File arsip sudah dihapus permanen")
    from pathlib import Path

    if not Path(row.path_arsip).exists():
        raise HTTPException(410, "File arsip tidak ada di penyimpanan")
    audit.log(db, user.id, "archive_download", {"file_id": row.id, "nama": row.nama_asli}, _ip(request))
    db.commit()
    return FileResponse(row.path_arsip, filename=row.nama_asli, media_type="application/octet-stream")


@router.post("/{file_id}/trash", response_model=ArchivedFileOut)
def move_to_trash(file_id: int, body: TrashRequest, request: Request, db: Session = Depends(get_db), user: User = HR_ONLY):
    """Pindahkan arsip ke tempat sampah dengan alasan (PRD F24). Hanya HR."""
    row = _get(db, file_id)
    if row.status != ArchiveStatus.aktif:
        raise HTTPException(409, "Hanya file aktif yang bisa dipindahkan ke tempat sampah")
    svc.trash(db, row, user.id, body.alasan)
    audit.log(db, user.id, "archive_trash", {"file_id": row.id, "nama": row.nama_asli, "alasan": body.alasan}, _ip(request))
    db.commit()
    return _out(row, svc.uploader_name(db, row.pengunggah_id))


@router.post("/{file_id}/restore", response_model=ArchivedFileOut)
def restore(file_id: int, request: Request, db: Session = Depends(get_db), user: User = HR_ONLY):
    row = _get(db, file_id)
    if row.status != ArchiveStatus.sampah:
        raise HTTPException(409, "Hanya file di tempat sampah yang bisa dipulihkan")
    svc.restore(row)
    audit.log(db, user.id, "archive_restore", {"file_id": row.id, "nama": row.nama_asli}, _ip(request))
    db.commit()
    return _out(row, svc.uploader_name(db, row.pengunggah_id))


@router.delete("/{file_id}", response_model=ArchivedFileOut)
def purge(file_id: int, request: Request, db: Session = Depends(get_db), user: User = HR_ONLY):
    """Hapus permanen. Hanya HR dan hanya setelah masa tunggu tempat sampah habis (PRD F24)."""
    row = _get(db, file_id)
    if row.status != ArchiveStatus.sampah:
        raise HTTPException(409, "File harus berada di tempat sampah sebelum dihapus permanen")
    if not svc.can_purge(row):
        raise HTTPException(
            409,
            f"Masa tunggu tempat sampah belum habis ({settings.archive_trash_days} hari). Gunakan Pulihkan bila masih diperlukan.",
        )
    svc.purge(row)
    audit.log(db, user.id, "archive_purge", {"file_id": row.id, "nama": row.nama_asli, "checksum": row.checksum}, _ip(request))
    db.commit()
    return _out(row, svc.uploader_name(db, row.pengunggah_id))
