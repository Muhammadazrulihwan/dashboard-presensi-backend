"""Ubah password akun yang sudah ada. Jalankan dari folder backend: python reset_password.py"""
import getpass
import sys

from sqlalchemy import func, select

from app.core.security import hash_password
from app.db import SessionLocal
from app.models import User

email = input("Email akun: ").strip().lower()
pw = getpass.getpass("Password baru (min 8 karakter): ")
if len(pw) < 8:
    sys.exit("Password minimal 8 karakter.")
with SessionLocal() as db:
    u = db.scalar(select(User).where(func.lower(User.email) == email))
    if u is None:
        sys.exit("Akun tidak ditemukan.")
    u.password_hash = hash_password(pw)
    u.is_active = True
    db.commit()
print("Password diubah.")
