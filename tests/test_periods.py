"""Tutup/buka periode (PRD F13): kunci upload & hitung ulang, status payroll, audit, akses HR."""
import io

import polars as pl
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, Period
from tests.conftest import as_dept
from tests.test_presensi import HEADER, upload

RANGE = {"from": "2026-10-01", "to": "2026-10-31"}


def close(client, tahun=2026, bulan=10, alasan="tutup buku"):
    return client.post("/periods/close", json={"tahun": tahun, "bulan": bulan, "alasan": alasan})


def reopen(client, tahun=2026, bulan=10, alasan="koreksi"):
    return client.post("/periods/reopen", json={"tahun": tahun, "bulan": bulan, "alasan": alasan})


def test_close_and_reopen_period(client):
    upload(client)  # data 2026-10
    r = close(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ditutup"] is True and body["periode"] == "2026-10"
    assert body["records"] > 0 and body["ditutup_oleh"] == "HR Default"

    # Menutup dua kali ditolak
    assert close(client).status_code == 409

    listed = client.get("/periods").json()
    assert any(p["periode"] == "2026-10" and p["ditutup"] for p in listed)

    # Buka kembali wajib alasan
    assert client.post("/periods/reopen", json={"tahun": 2026, "bulan": 10, "alasan": ""}).status_code == 422
    r = reopen(client)
    assert r.status_code == 200 and r.json()["ditutup"] is False
    assert reopen(client).status_code == 409  # belum ditutup


def test_closed_period_blocks_upload_and_recalculate(client):
    upload(client)
    close(client)

    # Upload template baru pada bulan yang sama ditolak
    revisi = HEADER + "001,Ani,2026-10-20,IT,,08:00,17:00,Hadir,\n"
    p = client.post("/uploads/preview", files={"file": ("p.csv", revisi, "text/csv")})
    assert p.status_code == 200 and p.json()["can_save"]
    c = client.post(f"/uploads/{p.json()['upload_id']}/confirm", json={"mode": "overwrite"})
    assert c.status_code == 409 and "ditutup" in c.json()["detail"]

    # Hitung ulang ditolak
    r = client.post("/settings/recalculate", json={"from": "2026-10-01", "to": "2026-10-31"})
    assert r.status_code == 409

    # Setelah dibuka kembali, boleh lagi
    reopen(client)
    assert client.post("/settings/recalculate", json={"from": "2026-10-01", "to": "2026-10-31"}).status_code == 200


def test_closed_period_blocks_delete_upload(client):
    """PRD F13: data periode tertutup tidak boleh diubah, termasuk dihapus dari Riwayat Upload."""
    body, _ = upload(client)
    close(client)
    uid = body["upload_id"]

    r = client.request("DELETE", f"/uploads/{uid}", json={"alasan": "uji"})
    assert r.status_code == 409 and "ditutup" in r.json()["detail"]
    assert client.get("/dashboard/summary").json()["current"]["records"] > 0

    # Hapus massal juga ditolak
    rb = client.post("/uploads/bulk-delete", json={"ids": [uid], "alasan": "uji"})
    assert rb.status_code == 409

    # Setelah dibuka kembali, hapus berhasil
    reopen(client)
    assert client.request("DELETE", f"/uploads/{uid}", json={"alasan": "uji"}).status_code == 200


def test_delete_upload_cleans_raw_sources(client):
    """Hapus paket empat sumber harus membersihkan data mentah, bukan meninggalkan baris yatim."""
    from tests.test_four_sources import activity_xlsx, approval_xlsx, report_xlsx, timeoff_xlsx, upload_package, confirm_package
    from app.models import AttendanceCorrection, RawActivity, RawReport, TimeOffRequest
    from sqlalchemy import func

    body = upload_package(client, {
        "activity": activity_xlsx(), "report": report_xlsx(),
        "timeoff": timeoff_xlsx(), "approval": approval_xlsx(),
    })
    confirm_package(client, body["upload_id"])

    def counts():
        with SessionLocal() as db:
            return tuple(db.scalar(select(func.count()).select_from(m)) or 0
                         for m in (RawActivity, RawReport, TimeOffRequest, AttendanceCorrection))

    assert all(n > 0 for n in counts())
    assert client.request("DELETE", f"/uploads/{body['upload_id']}", json={"alasan": "uji"}).status_code == 200
    assert counts() == (0, 0, 0, 0)


def test_payroll_status_periode(client):
    upload(client)
    r = client.get("/exports/payroll", params=RANGE)
    assert r.status_code == 200
    df = pl.read_excel(io.BytesIO(r.content), sheet_name="Rekap Payroll", engine="calamine")
    assert set(df["status_periode"].to_list()) == {"DRAFT"}
    assert "_DRAFT.xlsx" in r.headers["content-disposition"]

    close(client)
    r = client.get("/exports/payroll", params=RANGE)
    df = pl.read_excel(io.BytesIO(r.content), sheet_name="Rekap Payroll", engine="calamine")
    assert set(df["status_periode"].to_list()) == {"DITUTUP"}
    assert "_DITUTUP.xlsx" in r.headers["content-disposition"]


def test_period_actions_are_audited(client):
    upload(client)
    close(client)
    reopen(client)
    with SessionLocal() as db:
        actions = [r.action for r in db.scalars(select(AuditLog).order_by(AuditLog.id)).all()]
    assert "period_close" in actions and "period_reopen" in actions


def test_periods_hr_only(client):
    for dept in ("IT", "Audit", "Sales/Marketing"):
        as_dept(dept)
        assert client.get("/periods").status_code == 403, dept
        assert close(client).status_code == 403, dept
    as_dept("HR")
    assert client.get("/periods").status_code == 200


def test_list_periods_filters_by_year(client):
    upload(client)
    close(client)
    assert any(p["periode"] == "2026-10" for p in client.get("/periods", params={"tahun": 2026}).json())
    assert client.get("/periods", params={"tahun": 2025}).json() == []
