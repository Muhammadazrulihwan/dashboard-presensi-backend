"""Buat akun uji per departemen (IT, HR, Audit, Sales/Marketing) plus akun non-dashboard.

Cara pakai: dari folder backend, setelah `alembic upgrade head`, jalankan:
    python seed_users.py
Aman dijalankan berulang (akun yang sudah ada dilewati). Akun admin IT tetap dari ADMIN_EMAIL di .env.
Password semua akun uji di bawah ini: Uji12345!  (khusus pengujian, jangan dipakai di produksi)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from sqlalchemy import func, select  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402

PASSWORD = "Uji12345!"
ACCOUNTS = [
    ("it2@perusahaan.local", "Admin IT Kedua", "IT"),
    ("hr@perusahaan.local", "Staf HR", "HR"),
    ("audit@perusahaan.local", "Staf Audit", "Audit"),
    ("sales@perusahaan.local", "Staf Sales", "Sales/Marketing"),
    ("finance@perusahaan.local", "Staf Finance", None),  # di luar 4 departemen dashboard
]

if settings.is_production:
    sys.exit("Ditolak: seed_users.py berisi password uji dan tidak boleh dijalankan saat APP_ENV=production.")

# Skema dibuat lewat Alembic (alembic upgrade head), bukan create_all.
with SessionLocal() as db:
    for email, name, dept in ACCOUNTS:
        if db.scalar(select(User).where(func.lower(User.email) == email)):
            print("lewati (sudah ada):", email)
            continue
        db.add(User(email=email, full_name=name, password_hash=hash_password(PASSWORD), departemen=dept))
        print("dibuat:", email, "|", dept or "-")
    db.commit()
