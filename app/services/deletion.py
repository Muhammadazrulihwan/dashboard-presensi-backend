"""Penghapusan data sebuah upload secara menyeluruh.

Baris `uploads` tidak pernah dihapus fisik (hanya ditandai `deleted`) agar riwayat dan jejak audit
tetap ada. Akibatnya ON DELETE CASCADE tidak berjalan, sehingga data turunan harus dibersihkan
secara eksplisit di sini. Fungsi ini dipakai bersama oleh:
- `routers/uploads.py` (hapus satu / massal dari Riwayat Upload),
- `routers/sales.py` (hapus upload Kinerja Penjualan).

PRD F24: file asli tidak dimusnahkan; file dipindah ke tempat sampah arsip dan masih bisa dipulihkan.
"""
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    ArchiveStatus, ArchivedFile, Attendance, AttendanceCorrection, RawActivity, RawReport,
    SalesAssessment, SalesPerformance, SalesTarget, TimeOffRequest, Upload, UploadStatus,
)
from app.services import archive as archive_svc
from app.services.archive import archive_upload


def _is_sales_upload(db: Session, up: Upload) -> bool:
    """True bila upload ini milik Kinerja Penjualan (punya baris penjualan/target)."""
    has_perf = db.scalar(
        select(func.count()).select_from(SalesPerformance).where(SalesPerformance.upload_id == up.id)
    )
    has_target = db.scalar(
        select(func.count()).select_from(SalesTarget).where(SalesTarget.upload_id == up.id)
    )
    return bool(has_perf or has_target)


def _delete_sales_rows(db: Session, up: Upload) -> None:
    """Hapus baris penjualan/target milik upload ini beserta penilaiannya.

    `sales_assessments` tidak menyimpan `upload_id`; penilaian dihapus lewat pasangan
    (employee_id, periode) dari baris penjualan milik upload ini. Pasangan itu harus dibaca
    SEBELUM baris penjualan dihapus.
    """
    pairs = list(db.execute(
        select(SalesPerformance.employee_id, SalesPerformance.periode)
        .where(SalesPerformance.upload_id == up.id).distinct()
    ).all())
    if pairs:
        db.execute(delete(SalesAssessment).where(
            or_(*[and_(SalesAssessment.employee_id == eid, SalesAssessment.periode == per) for eid, per in pairs])
        ))
    db.execute(delete(SalesPerformance).where(SalesPerformance.upload_id == up.id))
    db.execute(delete(SalesTarget).where(SalesTarget.upload_id == up.id))


def delete_upload_rows(db: Session, ups: list[Upload], user_id: int, alasan: str) -> list[int]:
    """Bersihkan seluruh data turunan beberapa upload; file asli dijamin masuk tempat sampah arsip.

    Tidak commit; pemanggil yang commit dan mencatat audit. Mengembalikan id arsip yang dipindah
    ke tempat sampah.
    """
    archive_ids: list[int] = []
    for up in ups:
        jenis = "penjualan" if _is_sales_upload(db, up) else "presensi"

        archived = db.scalars(
            select(ArchivedFile).where(ArchivedFile.upload_id == up.id, ArchivedFile.status == ArchiveStatus.aktif)
        ).all()
        if not archived and Path(up.stored_path).exists():
            archive_upload(db, up, jenis, periode_awal=up.date_from, periode_akhir=up.date_to)
            db.flush()
            archived = db.scalars(
                select(ArchivedFile).where(ArchivedFile.upload_id == up.id, ArchivedFile.status == ArchiveStatus.aktif)
            ).all()
        for row in archived:
            archive_svc.trash(db, row, user_id, alasan)
            archive_ids.append(row.id)

        # Data presensi terpadu dan data mentah empat sumber.
        db.execute(delete(Attendance).where(Attendance.upload_id == up.id))
        db.execute(delete(RawActivity).where(RawActivity.upload_id == up.id))
        db.execute(delete(RawReport).where(RawReport.upload_id == up.id))
        db.execute(delete(TimeOffRequest).where(TimeOffRequest.upload_id == up.id))
        db.execute(delete(AttendanceCorrection).where(AttendanceCorrection.upload_id == up.id))

        # Data Kinerja Penjualan. Sebelumnya tabel ini TERLEWAT sehingga data penjualan tertinggal
        # sebagai data yatim dan tetap tampil di dashboard walau upload-nya sudah dihapus.
        _delete_sales_rows(db, up)

        up.status = UploadStatus.deleted
    return archive_ids
