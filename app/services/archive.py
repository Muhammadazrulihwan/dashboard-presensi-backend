"""Arsip file upload (PRD F22-F24).

File asli disalin ke folder arsip terpisah (`ARCHIVE_DIR`) begitu paket upload berhasil disimpan.
Nama file arsip tidak bisa menimpa file lain (UUID + nama asli), checksum SHA-256 disimpan agar
integritas bisa diperiksa. Penghapusan memakai tahap tempat sampah sebelum hapus permanen.
"""
import hashlib
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import ArchiveStatus, ArchivedFile, ArchivedFileEmployee, Upload, User


def _checksum(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_name(original: str) -> str:
    """Nama aman untuk filesystem: buang komponen path dan karakter aneh, sisakan ekstensi."""
    name = Path(original or "file").name
    cleaned = "".join(c if (c.isalnum() or c in "._-") else "_" for c in name).strip("._") or "file"
    return cleaned[:120]


def _archive_one(
    db: Session,
    up: Upload,
    jenis_sumber: str,
    src: Path,
    nama_asli: str,
    *,
    periode_awal: date | None,
    periode_akhir: date | None,
    employee_ids: set[str] | None,
) -> ArchivedFile | None:
    """Salin satu file ke arsip dan catat metadatanya. Tidak commit."""
    if not src.exists():
        return None

    settings.archive_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc)
    folder = settings.archive_dir / f"{today:%Y}" / f"{today:%m}" / jenis_sumber
    folder.mkdir(parents=True, exist_ok=True)

    dest = folder / f"{uuid.uuid4().hex}_{_safe_name(nama_asli)}"
    shutil.copy2(src, dest)

    row = ArchivedFile(
        upload_id=up.id,
        jenis_sumber=jenis_sumber,
        nama_asli=nama_asli,
        path_arsip=str(dest),
        ukuran=dest.stat().st_size,
        checksum=_checksum(dest),
        pengunggah_id=up.uploaded_by,
        periode_awal=periode_awal,
        periode_akhir=periode_akhir,
        status=ArchiveStatus.aktif,
    )
    db.add(row)
    db.flush()  # perlu id untuk baris employee di bawah
    for eid in sorted({e.strip() for e in (employee_ids or set()) if e and e.strip()}):
        db.add(ArchivedFileEmployee(archive_id=row.id, employee_id=eid))
    return row


def archive_upload(
    db: Session,
    up: Upload,
    jenis_sumber: str,
    *,
    periode_awal: date | None = None,
    periode_akhir: date | None = None,
    employee_ids: set[str] | None = None,
    extra_files: list | None = None,
) -> ArchivedFile | None:
    """Salin file upload ke arsip dan catat metadatanya. Tidak commit; pemanggil yang commit.

    Mengembalikan None bila file sumber sudah tidak ada (mis. sudah dibersihkan), supaya alur
    simpan data tidak gagal hanya karena arsip tidak bisa dibuat.

    `employee_ids` menyimpan Employee ID yang tercatat pada file agar arsip bisa dicari berdasarkan ID
    karyawan (PRD F23).

    `extra_files` = daftar baris `upload_files` pada paket empat sumber. Bila diisi, SEMUA file paket
    diarsipkan (PRD F22), bukan hanya file pertama. Baris pertama tetap diarsipkan dari `up.stored_path`
    untuk kompatibilitas, lalu sisanya dari `stored_path` masing-masing.
    """
    first = _archive_one(
        db, up, jenis_sumber, Path(up.stored_path), up.filename,
        periode_awal=periode_awal, periode_akhir=periode_akhir, employee_ids=employee_ids,
    )
    seen = {str(Path(up.stored_path))}
    for f in extra_files or []:
        path = getattr(f, "stored_path", None)
        nama = getattr(f, "nama_file", None)
        if not path or not nama or str(Path(path)) in seen:
            continue
        seen.add(str(Path(path)))
        _archive_one(
            db, up, jenis_sumber, Path(path), nama,
            periode_awal=periode_awal, periode_akhir=periode_akhir, employee_ids=employee_ids,
        )
    return first


def trash(db: Session, row: ArchivedFile, user_id: int, alasan: str | None) -> None:
    row.status = ArchiveStatus.sampah
    row.dihapus_at = datetime.now(timezone.utc)
    row.dihapus_oleh = user_id
    row.alasan_hapus = (alasan or "").strip() or None


def restore(row: ArchivedFile) -> None:
    row.status = ArchiveStatus.aktif
    row.dihapus_at = None
    row.dihapus_oleh = None
    row.alasan_hapus = None


def purge(row: ArchivedFile) -> None:
    """Hapus file fisik lalu tandai baris sebagai dihapus permanen."""
    Path(row.path_arsip).unlink(missing_ok=True)
    row.status = ArchiveStatus.dihapus


def can_purge(row: ArchivedFile) -> bool:
    """Hapus permanen hanya setelah masa tunggu tempat sampah habis."""
    if row.status != ArchiveStatus.sampah or row.dihapus_at is None:
        return False
    d = row.dihapus_at
    if d.tzinfo is None:  # SQLite menyimpan UTC tanpa zona
        d = d.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) >= d + timedelta(days=settings.archive_trash_days)


def verify(row: ArchivedFile) -> bool:
    """True bila file arsip ada dan checksum-nya masih cocok."""
    p = Path(row.path_arsip)
    return p.exists() and _checksum(p) == row.checksum


def uploader_name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    u = db.get(User, user_id)
    return u.full_name if u else None


def uploader_names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all()
    return {r.id: r.full_name for r in rows}


def employee_ids_map(db: Session, archive_ids: list[int]) -> dict[int, list[str]]:
    """Employee ID per file arsip, untuk ditampilkan di daftar arsip (PRD F23)."""
    if not archive_ids:
        return {}
    rows = db.execute(
        select(ArchivedFileEmployee.archive_id, ArchivedFileEmployee.employee_id)
        .where(ArchivedFileEmployee.archive_id.in_(archive_ids))
        .order_by(ArchivedFileEmployee.employee_id)
    ).all()
    out: dict[int, list[str]] = {}
    for archive_id, eid in rows:
        out.setdefault(archive_id, []).append(eid)
    return out
