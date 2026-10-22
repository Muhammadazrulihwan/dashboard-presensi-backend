"""Bersihkan data Kinerja Penjualan yatim (sisa upload yang sudah dihapus).

Bug lama: menghapus upload tidak membersihkan tabel `sales_performance`, `sales_targets`, dan
`sales_assessments`, sehingga data penjualan tetap tampil walau upload-nya sudah berstatus deleted.

Skrip ini menghapus baris penjualan/target yang `upload_id`-nya sudah tidak ada atau berstatus
`deleted`, beserta penilaian (employee_id, periode) yang tidak lagi punya baris penjualan.

Cara pakai (dari folder backend, memakai DATABASE_URL di .env):
    python cleanup_orphan_sales.py --dry-run     # lihat apa yang akan dihapus
    python cleanup_orphan_sales.py               # jalankan penghapusan

Aman dijalankan berulang: hanya baris yatim yang disentuh.
"""
import sys

from sqlalchemy import and_, delete, func, or_, select

from app.db import SessionLocal
from app.models import SalesAssessment, SalesPerformance, SalesTarget, Upload, UploadStatus


def main(dry_run: bool) -> None:
    with SessionLocal() as db:
        # Upload yang dianggap "hidup": ada barisnya dan tidak berstatus deleted.
        live_ids = set(db.scalars(
            select(Upload.id).where(Upload.status != UploadStatus.deleted)
        ).all())

        perf_all = db.scalar(select(func.count()).select_from(SalesPerformance)) or 0
        target_all = db.scalar(select(func.count()).select_from(SalesTarget)) or 0
        assess_all = db.scalar(select(func.count()).select_from(SalesAssessment)) or 0

        # Baris penjualan/target yang upload_id-nya tidak ada di daftar hidup.
        if live_ids:
            perf_orphan_cond = SalesPerformance.upload_id.notin_(live_ids)
            target_orphan_cond = SalesTarget.upload_id.notin_(live_ids)
        else:
            perf_orphan_cond = SalesPerformance.id.is_not(None)
            target_orphan_cond = SalesTarget.id.is_not(None)

        perf_orphan = db.scalar(select(func.count()).select_from(SalesPerformance).where(perf_orphan_cond)) or 0
        target_orphan = db.scalar(select(func.count()).select_from(SalesTarget).where(target_orphan_cond)) or 0

        # Pasangan (employee_id, periode) yang masih punya baris penjualan HIDUP (bukan yatim).
        if live_ids:
            live_pairs = set(db.execute(
                select(SalesPerformance.employee_id, SalesPerformance.periode)
                .where(SalesPerformance.upload_id.in_(live_ids)).distinct()
            ).all())
        else:
            live_pairs = set()
        orphan_assess = [
            a for a in db.scalars(select(SalesAssessment)).all()
            if (a.employee_id, a.periode) not in live_pairs
        ]

        print("=== RINGKASAN SEBELUM ===")
        print(f"sales_performance : {perf_all} (yatim: {perf_orphan})")
        print(f"sales_targets     : {target_all} (yatim: {target_orphan})")
        print(f"sales_assessments : {assess_all} (yatim: {len(orphan_assess)})")
        print(f"upload hidup      : {len(live_ids)}")

        if dry_run:
            print("\n--dry-run: tidak ada yang dihapus.")
            for a in orphan_assess[:20]:
                print(f"  penilaian yatim: {a.employee_id} {a.periode} status={a.status.value}")
            return

        if perf_orphan:
            db.execute(delete(SalesPerformance).where(perf_orphan_cond))
        if target_orphan:
            db.execute(delete(SalesTarget).where(target_orphan_cond))
        if orphan_assess:
            db.execute(delete(SalesAssessment).where(
                or_(*[and_(SalesAssessment.employee_id == a.employee_id,
                           SalesAssessment.periode == a.periode) for a in orphan_assess])
            ))
        db.commit()

        print("\n=== RINGKASAN SESUDAH ===")
        print(f"sales_performance : {db.scalar(select(func.count()).select_from(SalesPerformance)) or 0}")
        print(f"sales_targets     : {db.scalar(select(func.count()).select_from(SalesTarget)) or 0}")
        print(f"sales_assessments : {db.scalar(select(func.count()).select_from(SalesAssessment)) or 0}")
        print("Selesai.")


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
