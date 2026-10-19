from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Upload, UploadStatus

FAILED_KEEP_DAYS = 30      # riwayat "Gagal" disimpan sebulan, lalu dihapus
ABANDONED_AFTER_HOURS = 24  # upload yang tidak pernah dikonfirmasi


def discard_file(path: str) -> None:
    """Hapus file di storage. Gagal menghapus (mis. file masih terbuka di Windows) tidak boleh menggagalkan request."""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def purge_stale_uploads(db: Session) -> int:
    now = datetime.now(timezone.utc)
    stale = db.scalars(
        select(Upload).where(
            or_(
                and_(Upload.status == UploadStatus.failed, Upload.created_at < now - timedelta(days=FAILED_KEEP_DAYS)),
                and_(
                    Upload.status.in_([UploadStatus.pending, UploadStatus.validated]),
                    Upload.created_at < now - timedelta(hours=ABANDONED_AFTER_HOURS),
                ),
            )
        )
    ).all()
    for up in stale:
        discard_file(up.stored_path)
        db.delete(up)
    db.commit()
    return len(stale)
