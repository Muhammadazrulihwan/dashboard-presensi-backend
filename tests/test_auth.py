from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import delete, select

from app.core.config import DEV_JWT_SECRET, settings
from app.core.security import create_token, hash_password, verify_password
from app.db import SessionLocal
from app.models import AuditLog, User
from app.routers.auth import reset_rate_limit

pytestmark = pytest.mark.real_auth

PW = "rahasia-123"


@pytest.fixture(autouse=True)
def fresh_auth(client):
    reset_rate_limit()
    client.cookies.clear()
    yield
    client.cookies.clear()
    with SessionLocal() as db:
        db.execute(delete(AuditLog))
        db.execute(delete(User).where(User.email.like("%@tes.local")))
        db.commit()


def make_user(email="hr@tes.local", dept="HR", active=True, password=PW) -> User:
    with SessionLocal() as db:
        u = User(email=email, full_name="Tes " + email, password_hash=hash_password(password) if password else "!",
                 departemen=dept, is_active=active)
        db.add(u)
        db.commit()
        return u


def login(client, email="hr@tes.local", password=PW):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_password_hash_roundtrip():
    h = hash_password(PW)
    assert h != PW and h.startswith("scrypt$")
    assert verify_password(PW, h) and not verify_password("salah", h)
    assert hash_password(PW) != h  # salt acak
    assert not verify_password(PW, "!") and not verify_password(PW, "")  # user dummy lama dan data rusak


def test_endpoints_require_login(client):
    assert client.get("/health").status_code == 200
    for path in ["/auth/me", "/dashboard/summary", "/dashboard/meta", "/uploads", "/uploads/template", "/settings/shifts", "/exports/excel"]:
        assert client.get(path).status_code == 401, path


def test_login_success_sets_httponly_cookie_and_session_works(client):
    make_user()
    r = login(client)
    assert r.status_code == 200
    assert r.json() == {"id": r.json()["id"], "email": "hr@tes.local", "full_name": "Tes hr@tes.local", "departemen": "HR", "is_approver": False}
    cookie = r.headers["set-cookie"].lower()
    assert settings.cookie_name in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert "password" not in r.text

    assert client.get("/auth/me").json()["email"] == "hr@tes.local"
    assert client.get("/settings/shifts").status_code == 200  # aksi khusus departemen HR
    assert client.get("/dashboard/summary").status_code == 200


def test_email_case_insensitive(client):
    make_user()
    assert login(client, email="  HR@Tes.Local ").status_code == 200


def test_wrong_password_and_unknown_email_give_same_error(client):
    make_user()
    a = login(client, password="salah")
    b = login(client, email="tidakada@tes.local")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "Email atau password salah"}
    assert settings.cookie_name not in a.headers.get("set-cookie", "")
    assert client.get("/auth/me").status_code == 401


def test_inactive_and_legacy_dummy_cannot_login(client):
    make_user("off@tes.local", active=False)
    make_user("dummy@tes.local", password=None)  # password_hash "!"
    assert login(client, "off@tes.local").status_code == 401
    assert login(client, "dummy@tes.local", "!").status_code == 401


def test_deactivated_after_login_is_cut_off(client):
    u = make_user()
    assert login(client).status_code == 200
    with SessionLocal() as db:
        db.get(User, u.id).is_active = False
        db.commit()
    assert client.get("/auth/me").status_code == 401


def test_logout_clears_session(client):
    make_user()
    login(client)
    assert client.get("/auth/me").status_code == 200
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401
    assert client.post("/auth/logout").status_code == 204  # tanpa sesi pun tidak error


def test_expired_and_forged_tokens_rejected(client):
    u = make_user()
    expired = jwt.encode({"sub": str(u.id), "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, settings.jwt_secret, algorithm="HS256")
    forged = jwt.encode({"sub": str(u.id), "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "kunci-lain", algorithm="HS256")
    none_alg = jwt.encode({"sub": str(u.id)}, "", algorithm="none")
    for bad in (expired, forged, none_alg, "bukan-jwt"):
        client.cookies.clear()
        client.cookies.set(settings.cookie_name, bad)
        assert client.get("/auth/me").status_code == 401
    client.cookies.set(settings.cookie_name, create_token(u.id))
    assert client.get("/auth/me").status_code == 200


def test_lockout_after_repeated_failures(client):
    make_user()
    for _ in range(settings.login_max_attempts):
        assert login(client, password="salah").status_code == 401
    r = login(client, password="salah")
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert login(client).status_code == 429  # password benar pun ditolak selama dikunci
    assert login(client, email="lain@tes.local").status_code == 401  # kunci per email dan IP


def test_success_resets_failure_counter(client):
    make_user()
    for _ in range(settings.login_max_attempts - 1):
        login(client, password="salah")
    assert login(client).status_code == 200
    for _ in range(settings.login_max_attempts - 1):
        assert login(client, password="salah").status_code == 401


def test_rate_limit_persists_in_database(client):
    """PRD Bab 12: pembatas login disimpan di DB, jadi berlaku lintas worker/proses."""
    from app.models import LoginAttempt
    from app.services import login_limiter

    make_user()
    email, ip = "hr@tes.local", "testclient"
    for _ in range(settings.login_max_attempts):
        assert login(client, password="salah").status_code == 401

    # Baris tersimpan di database, bukan memori proses.
    with SessionLocal() as db:
        row = db.scalar(select(LoginAttempt).where(LoginAttempt.email == email, LoginAttempt.ip_address == ip))
        assert row is not None and row.gagal == settings.login_max_attempts
        assert row.terkunci_sampai is not None
        # Limiter membaca dari DB: proses lain (sesi DB baru) juga melihat kunci yang sama.
        assert login_limiter.locked_seconds(db, email, ip) > 0

    # Kunci kedaluwarsa: baris dibersihkan dan login kembali boleh.
    with SessionLocal() as db:
        row = db.scalar(select(LoginAttempt).where(LoginAttempt.email == email))
        row.terkunci_sampai = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    assert login(client).status_code == 200


def test_rate_limit_is_per_email_and_ip(client):
    make_user()
    for _ in range(settings.login_max_attempts):
        login(client, password="salah")
    assert login(client).status_code == 429
    assert login(client, email="lain@tes.local").status_code == 401  # email lain tidak terkunci


def test_access_enforced_by_department_with_real_login(client):
    make_user("sales@tes.local", dept="Sales/Marketing")
    assert login(client, "sales@tes.local").status_code == 200
    assert client.get("/dashboard/summary").status_code == 200       # departemen dashboard
    assert client.get("/dashboard/records").status_code == 200       # departemen kinerja
    assert client.get("/settings/shifts").status_code == 403         # bukan HR
    assert client.get("/uploads").status_code == 403                 # bukan HR
    assert client.get("/admin/users").status_code == 403             # bukan IT


def test_it_only_manages_users(client):
    make_user("it@tes.local", dept="IT")
    assert login(client, "it@tes.local").status_code == 200
    assert client.get("/admin/users").status_code == 200
    assert client.get("/dashboard/records").status_code == 200       # IT boleh lihat daftar presensi
    assert client.get("/dashboard/top-employees").status_code == 403  # tetapi tidak menilai kinerja
    assert client.get("/settings/shifts").status_code == 403


def test_audit_trail(client):
    u = make_user()
    login(client, password="salah")
    login(client)
    with SessionLocal() as db:
        rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    assert [r.action for r in rows] == ["login_failed", "login"]
    assert rows[0].user_id == u.id and rows[0].detail == {"email": "hr@tes.local"}
    assert "password" not in str(rows[0].detail) and "salah" not in str(rows[0].detail)


def test_ensure_admin_creates_and_upgrades_legacy_user(monkeypatch):
    from app.services.bootstrap import ensure_admin

    monkeypatch.setattr(settings, "admin_email", "Admin@Tes.Local")
    monkeypatch.setattr(settings, "admin_password", "password-awal-1")
    monkeypatch.setattr(settings, "admin_name", "Admin Awal")
    with SessionLocal() as db:
        ensure_admin(db)
        a = db.scalar(select(User).where(User.email == "admin@tes.local"))
        assert a and a.departemen == "IT" and verify_password("password-awal-1", a.password_hash)

        monkeypatch.setattr(settings, "admin_password", "diganti-lain-99")
        ensure_admin(db)  # akun sudah ada: password tidak ditimpa
        db.refresh(a)
        assert verify_password("password-awal-1", a.password_hash)

        monkeypatch.setattr(settings, "admin_email", "kedua@tes.local")
        monkeypatch.setattr(settings, "admin_password", "password-kedua-1")
        legacy = db.get(User, 1)
        was_dummy = legacy is not None and legacy.password_hash == "!"
        ensure_admin(db)
        if was_dummy:  # id 1 dipertahankan agar riwayat upload tidak yatim
            db.refresh(legacy)
            assert legacy.email == "kedua@tes.local" and verify_password("password-kedua-1", legacy.password_hash)
        db.execute(delete(User).where(User.email.like("%@tes.local")))
        db.commit()


def test_production_refuses_default_secret(monkeypatch):
    from app.services.bootstrap import check_production_settings

    # Nilai dipaksa eksplisit agar tes tidak bergantung pada isi .env di komputer yang menjalankannya.
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "jwt_secret", DEV_JWT_SECRET)
    monkeypatch.setattr(settings, "cookie_secure", False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        check_production_settings()
    monkeypatch.setattr(settings, "jwt_secret", "x" * 40)
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        check_production_settings()
    monkeypatch.setattr(settings, "cookie_secure", True)
    check_production_settings()
