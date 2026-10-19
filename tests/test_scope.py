"""Batas data karyawan menurut departemen akun (PRD v0.4): Sales/Marketing hanya melihat karyawan Sales/Marketing."""
from tests.conftest import as_dept
from tests.test_presensi import HEADER, upload

CSV = HEADER + "\n".join([
    "S01,Sari Sales,2026-10-05,Sales,,08:00,17:00,Hadir,",
    "S01,Sari Sales,2026-10-06,Sales,,08:20,17:00,Hadir,",
    "M01,Maya Marketing,2026-10-05,Marketing,,08:00,17:00,Hadir,",
    "I01,Ilham IT,2026-10-05,IT,,08:00,17:00,Hadir,",
    "H01,Hana HR,2026-10-05,HR,,08:45,17:00,Hadir,",
    "A01,Adi Audit,2026-10-05,Audit,,08:00,17:00,Hadir,",
]) + "\n"
RANGE = {"from": "2026-10-05", "to": "2026-10-09"}


def seed(client):
    upload(client, CSV)


def niks(client, **params):
    r = client.get("/dashboard/records", params={**RANGE, "page_size": 100, **params})
    assert r.status_code == 200, r.text
    return {x["nik"] for x in r.json()["items"]}


def test_sales_sees_only_sales_and_marketing_staff(client):
    seed(client)
    as_dept("Sales/Marketing")
    assert niks(client) == {"S01", "M01"}
    meta = client.get("/dashboard/meta").json()
    assert meta["departemen"] == ["Marketing", "Sales"] and meta["min_date"] == "2026-10-05"
    s = client.get("/dashboard/summary", params=RANGE).json()["current"]
    assert s["hari_kerja"] == 3                       # S01 dua hari + M01 satu hari, tanpa IT, HR, Audit
    assert [d["departemen"] for d in client.get("/dashboard/by-department", params=RANGE).json()] == ["Marketing", "Sales"]
    assert client.get("/dashboard/employee/S01", params=RANGE).status_code == 200


def test_sales_cannot_reach_other_departments_by_any_route(client):
    seed(client)
    as_dept("Sales/Marketing")
    assert client.get("/dashboard/employee/I01", params=RANGE).status_code == 404          # NIK di luar batas
    assert client.get("/exports/employee/pdf", params={**RANGE, "nik": "H01"}).status_code == 404
    assert niks(client, departemen="IT") == set()                                         # memilih divisi lain tetap kosong
    assert niks(client, karyawan="Ilham") == set()                                         # pencarian nama tidak membocorkan
    assert {t["nik"] for t in client.get("/dashboard/top-employees", params=RANGE).json()} <= {"S01", "M01"}
    assert client.get("/exports/excel", params=RANGE).status_code == 200


def test_hr_audit_it_see_everyone(client):
    seed(client)
    for dept in ("HR", "Audit"):
        as_dept(dept)
        assert niks(client) == {"S01", "M01", "I01", "H01", "A01"}, dept
        assert len(client.get("/dashboard/meta").json()["departemen"]) == 5
    as_dept("IT")
    assert len(client.get("/dashboard/meta").json()["departemen"]) == 5
    assert client.get("/dashboard/summary", params=RANGE).json()["current"]["hari_kerja"] == 6


def test_scope_names_are_case_insensitive_and_configurable(client, monkeypatch):
    from app.core.config import settings
    seed(client)
    as_dept("Sales/Marketing")
    monkeypatch.setattr(settings, "sales_org_names", " SALES ")
    assert niks(client) == {"S01"}
