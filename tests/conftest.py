import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
# Default: SQLite sementara. Untuk menguji perilaku MySQL (PRD Bab 9), set TEST_DATABASE_URL,
# mis. mysql+pymysql://presensi_app:...@127.0.0.1:3306/presensi_test?charset=utf8mb4
# Database itu harus sudah dibuat kosong; skema dibangun lewat Alembic seperti produksi.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL or f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/uploads"
os.environ["ARCHIVE_DIR"] = f"{_tmp}/archive"
# Dipaksa agar hasil tes tidak bergantung pada isi .env di komputer masing-masing (variabel lingkungan menang atas .env).
os.environ["APP_ENV"] = "development"
os.environ["COOKIE_SECURE"] = "false"
os.environ["JWT_SECRET"] = "secret-khusus-tes-" + "x" * 32
os.environ["ADMIN_EMAIL"] = "admin@tes-seed.local"
os.environ["ADMIN_PASSWORD"] = "password-seed-123"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, engine  # noqa: E402
from app.deps import get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (
    ArchivedFile, ArchivedFileEmployee, Attendance, AttendanceCorrection, AuditLog, Employee, Holiday,
    LoginAttempt, Period, RawActivity, RawReport, SalesAssessment, SalesPerformance, SalesTarget, ShiftRule,
    TimeOffRequest, Upload, UploadFile, User,
)

USING_MYSQL = engine.dialect.name == "mysql"


def _run_migrations() -> None:
    """Buat skema database tes lewat Alembic (bukan create_all), sama seperti produksi."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")


def _reset_mysql_schema() -> None:
    """Untuk MySQL uji: turunkan semua migrasi lalu naik lagi agar tiap sesi tes mulai dari skema bersih."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: tes memakai login sungguhan, tanpa override get_current_user")
    if USING_MYSQL:
        # MySQL tidak mendukung DDL dalam transaksi; bangun ulang skema agar sisa data sesi sebelumnya tidak mengganggu.
        _reset_mysql_schema()
    else:
        _run_migrations()


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_db(client, request):
    with SessionLocal() as db:
        # Urutan hapus: anak lebih dulu agar foreign key MySQL tidak menolak.
        db.execute(delete(Attendance))
        db.execute(delete(UploadFile))
        db.execute(delete(ArchivedFileEmployee))
        db.execute(delete(ArchivedFile))
        db.execute(delete(SalesAssessment))
        db.execute(delete(SalesPerformance))
        db.execute(delete(SalesTarget))
        db.execute(delete(Upload))
        db.execute(delete(RawActivity))
        db.execute(delete(RawReport))
        db.execute(delete(TimeOffRequest))
        db.execute(delete(AttendanceCorrection))
        db.execute(delete(Employee))
        db.execute(delete(Period))
        db.execute(delete(Holiday))
        db.execute(delete(AuditLog))
        db.execute(delete(LoginAttempt))
        db.execute(delete(ShiftRule).where(ShiftRule.kode != "REGULER"))
        reg = db.get(ShiftRule, "REGULER")
        reg.jam_masuk_menit, reg.jam_pulang_menit, reg.toleransi_menit = 480, 1020, 0
        db.commit()
    app.dependency_overrides.clear()
    if not request.node.get_closest_marker("real_auth"):
        # Tes non-auth berjalan sebagai akun departemen HR (akses paling luas), tanpa login lewat HTTP.
        # Akun disimpan ke DB agar tetap valid saat join (mis. daftar upload terhubung ke pengunggah).
        with SessionLocal() as db:
            hr = db.scalar(select(User).where(User.email == "hr@tes-default.local"))
            if hr is None:
                hr = User(email="hr@tes-default.local", full_name="HR Default", password_hash="!", departemen="HR")
                db.add(hr)
            else:
                hr.departemen, hr.is_active = "HR", True
            db.commit()
            db.refresh(hr)
        app.dependency_overrides[get_current_user] = lambda: hr
    yield
    app.dependency_overrides.clear()


def as_dept(departemen: str | None):
    """Pura-pura login sebagai akun di departemen tertentu (tes login sungguhan ada di test_auth.py).

    User disimpan ke database dengan id sungguhan agar foreign key audit_log.user_id terpenuhi di MySQL
    (SQLite tidak menegakkan FK). Akun dibuat nonaktif: override get_current_user melewati cek is_active,
    dan status nonaktif menjaga hitungan "admin IT aktif terakhir" tidak ikut terpengaruh.
    """
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "dept-tes@conftest.local"))
        if user is None:
            user = User(email="dept-tes@conftest.local", full_name="Akun Dept Tes", password_hash="!")
            db.add(user)
        user.departemen, user.is_active = departemen, False
        db.commit()
        db.refresh(user)
        # expire_on_commit=False: atribut tetap terbaca setelah sesi ditutup.
        db.expunge(user)
    app.dependency_overrides[get_current_user] = lambda: user
