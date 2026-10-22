"""Bersihkan data Sales/Marketing lama yang masih memakai NIK tim IT (LS019/LS021/LS417),
lalu siap diunggah ulang memakai file 8_*_Sales dengan NIK SL001/SL002/SL003.

Jalankan dari folder backend: python cleanup_old_sales_demo.py
"""
from sqlalchemy import delete, or_, select

from app.db import SessionLocal
from app.models import Attendance, SalesAssessment, SalesPerformance, SalesTarget, Upload, UploadStatus

OLD_IDS = ["LS019", "LS021", "LS417"]

with SessionLocal() as db:
    # Hanya hapus baris yang berdivisi Sales/Marketing, jangan sentuh data IT.
    rows = db.scalars(select(Attendance).where(
        Attendance.departemen == "Sales/Marketing", Attendance.nik.in_(OLD_IDS)
    )).all()
    print("attendance Sales lama (NIK IT) yang dihapus:", len(rows))
    for r in rows:
        db.delete(r)

    # Data penjualan yang memakai NIK lama.
    perf = db.scalars(select(SalesPerformance).where(SalesPerformance.employee_id.in_(OLD_IDS))).all()
    print("sales_performance lama dihapus:", len(perf))
    for r in perf:
        db.delete(r)
    tgt = db.scalars(select(SalesTarget).where(SalesTarget.employee_id.in_(OLD_IDS))).all()
    print("sales_targets lama dihapus:", len(tgt))
    for r in tgt:
        db.delete(r)
    ass = db.scalars(select(SalesAssessment).where(SalesAssessment.employee_id.in_(OLD_IDS))).all()
    print("sales_assessments lama dihapus:", len(ass))
    for r in ass:
        db.delete(r)

    db.commit()
print("Selesai.")
