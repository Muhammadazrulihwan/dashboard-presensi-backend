import pytest
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import AuditLog, User
from tests.conftest import as_dept


@pytest.fixture(autouse=True)
def _as_it(client):
    """Kelola user hanya untuk departemen IT; tes di sini berjalan sebagai akun IT."""
    as_dept("IT")


def _cleanup():
    with SessionLocal() as db:
        db.execute(delete(User).where(User.email.like("%@admin-tes.local")))
        db.commit()


def test_list_and_create_user(client):
    r = client.post("/admin/users", json={"email": "Baru@Admin-Tes.Local", "full_name": "Baru Saja", "password": "rahasia-123", "departemen": "HR"})
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "baru@admin-tes.local" and body["departemen"] == "HR" and body["is_active"] is True
    assert "password" not in r.text

    users = client.get("/admin/users").json()
    assert any(u["email"] == "baru@admin-tes.local" for u in users)
    _cleanup()


def test_invalid_department_rejected(client):
    r = client.post("/admin/users", json={"email": "x@admin-tes.local", "full_name": "X", "password": "rahasia-123", "departemen": "Divisi Ngawur"})
    assert r.status_code == 422
    _cleanup()


def test_create_duplicate_email_rejected(client):
    payload = {"email": "dup@admin-tes.local", "full_name": "Dup", "password": "rahasia-123", "departemen": "IT"}
    assert client.post("/admin/users", json=payload).status_code == 201
    assert client.post("/admin/users", json=payload).status_code == 409
    _cleanup()


def test_short_password_rejected(client):
    r = client.post("/admin/users", json={"email": "x@admin-tes.local", "full_name": "X", "password": "pendek", "departemen": "IT"})
    assert r.status_code == 422
    _cleanup()


def test_update_and_reset_password_and_deactivate(client):
    uid = client.post("/admin/users", json={"email": "u@admin-tes.local", "full_name": "U", "password": "rahasia-123", "departemen": "Sales/Marketing"}).json()["id"]
    r = client.patch(f"/admin/users/{uid}", json={"departemen": "HR", "full_name": "U Baru"})
    assert r.status_code == 200 and r.json()["departemen"] == "HR"
    assert client.post(f"/admin/users/{uid}/password", json={"password": "ganti-456789"}).status_code == 204
    assert client.delete(f"/admin/users/{uid}").status_code == 204
    row = next(u for u in client.get("/admin/users").json() if u["id"] == uid)
    assert row["is_active"] is False
    _cleanup()


def test_departemen_cleared_with_empty_string(client):
    uid = client.post("/admin/users", json={"email": "d@admin-tes.local", "full_name": "D", "password": "rahasia-123", "departemen": "IT"}).json()["id"]
    r = client.patch(f"/admin/users/{uid}", json={"departemen": ""})
    assert r.json()["departemen"] is None
    _cleanup()


def _as_seed_admin(client):
    from app.deps import get_current_user
    from app.main import app

    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == "admin@tes-seed.local"))
    app.dependency_overrides[get_current_user] = lambda: admin
    return admin


def test_cannot_deactivate_self(client):
    admin = _as_seed_admin(client)
    assert client.delete(f"/admin/users/{admin.id}").status_code == 409


def test_cannot_remove_last_active_it_admin(client):
    admin = _as_seed_admin(client)
    # Mengubah departemen admin IT terakhir jadi HR ditolak.
    assert client.patch(f"/admin/users/{admin.id}", json={"departemen": "HR"}).status_code == 409
    assert client.patch(f"/admin/users/{admin.id}", json={"is_active": False}).status_code == 409


def test_only_it_can_manage_users(client):
    as_dept("HR")
    assert client.get("/admin/users").status_code == 403
    as_dept("Audit")
    assert client.post("/admin/users", json={"email": "z@admin-tes.local", "full_name": "Z", "password": "rahasia-123", "departemen": "IT"}).status_code == 403
    as_dept(None)
    assert client.get("/admin/users").status_code == 403


def test_actions_are_audited(client):
    uid = client.post("/admin/users", json={"email": "a@admin-tes.local", "full_name": "A", "password": "rahasia-123", "departemen": "Audit"}).json()["id"]
    client.delete(f"/admin/users/{uid}")
    with SessionLocal() as db:
        actions = [r.action for r in db.scalars(select(AuditLog).order_by(AuditLog.id)).all()]
    assert "user_create" in actions and "user_deactivate" in actions
    _cleanup()


def test_approver_only_for_sales_marketing(client):
    r = client.post("/admin/users", json={"email": "appr@admin-tes.local", "full_name": "Appr", "password": "rahasia-123", "departemen": "HR"})
    uid = r.json()["id"]
    # departemen non-Sales tidak boleh jadi penyetuju
    assert client.patch(f"/admin/users/{uid}", json={"is_approver": True}).status_code == 422
    # Sales/Marketing boleh
    assert client.patch(f"/admin/users/{uid}", json={"departemen": "Sales/Marketing", "is_approver": True}).json()["is_approver"] is True
    # pindah departemen mencabut hak penyetuju
    moved = client.patch(f"/admin/users/{uid}", json={"departemen": "Audit"}).json()
    assert moved["departemen"] == "Audit" and moved["is_approver"] is False
    _cleanup()
