"""Kinerja Penjualan (Rilis E): upload dua sheet, dashboard tiga tingkat, hak akses, dan Persetujuan Penilaian."""
import io

import openpyxl
import pytest
from sqlalchemy import delete

from app.db import SessionLocal
from app.deps import get_current_user
from app.main import app
from app.models import SalesAssessment, SalesPerformance, SalesTarget, User

P = "2026-10"


@pytest.fixture(autouse=True)
def clean_sales(client):
    with SessionLocal() as db:
        for m in (SalesAssessment, SalesPerformance, SalesTarget):
            db.execute(delete(m))
        db.commit()


def login(departemen, approver=False, email="sales.tes@x"):
    with SessionLocal() as db:
        u = db.query(User).filter_by(email=email).first() or User(email=email, full_name="Akun Tes", password_hash="!")
        u.departemen, u.is_approver, u.is_active = departemen, approver, True
        db.add(u)
        db.commit()
        db.refresh(u)
    app.dependency_overrides[get_current_user] = lambda: u


def sales_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Penjualan"
    ws.append(["employee_id", "nama", "cabang", "periode", "penjualan", "penerimaan", "piutang_outlet", "sisa_bad_debt"])
    ws.append(["S001", "Andi", "Bandung", P, 150_000_000, 120_000_000, 30_000_000, 0])
    ws.append(["S002", "Budi", "Bandung", P, 80_000_000, 70_000_000, 10_000_000, 0])
    wt = wb.create_sheet("Target")
    wt.append(["level", "cabang", "employee_id", "periode", "target_penjualan"])
    wt.append(["Perusahaan", None, None, P, 500_000_000])
    wt.append(["Cabang", "Bandung", None, P, 250_000_000])
    wt.append(["Salesman", "Bandung", "S001", P, 100_000_000])
    wt.append(["Salesman", "Bandung", "S002", P, 100_000_000])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload_ok(client):
    r = client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())})
    assert r.status_code == 200, r.text
    assert r.json()["can_save"] is True, r.json()
    c = client.post(f"/sales/{r.json()['upload_id']}/confirm")
    assert c.status_code == 200, c.text
    return c.json()


def test_upload_and_dashboards(client):
    login("Sales/Marketing")
    out = upload_ok(client)
    assert out["inserted_performance"] == 2 and out["inserted_targets"] == 4 and out["assessments_created"] == 2

    login("HR", email="hr.sales.tes@x")
    rows = {r["employee_id"]: r for r in client.get("/sales/salesman", params={"periode": P}).json()}
    assert rows["S001"]["status_kelayakan"] == "Layak" and round(rows["S001"]["capaian_pct"]) == 150
    assert rows["S002"]["status_kelayakan"] == "Tidak Layak" and rows["S002"]["assessment_status"] == "Draf"
    assert (rows["S001"]["peringkat_cabang"], rows["S002"]["peringkat_cabang"]) == (1, 2)   # peringkat dalam cabang
    cab = client.get("/sales/cabang", params={"periode": P}).json()
    assert cab[0]["cabang"] == "Bandung" and cab[0]["total_penjualan"] == 230_000_000 and cab[0]["salesman_layak"] == 1
    per = client.get("/sales/perusahaan", params={"periode": P}).json()
    assert per["total_penjualan"] == 230_000_000 and per["target_perusahaan"] == 500_000_000


def test_upload_sales_blocked_when_period_closed(client):
    """PRD F13: periode tertutup tidak boleh diubah, termasuk lewat unggah Kinerja Penjualan."""
    tahun, bulan = (int(x) for x in P.split("-"))
    login("HR", email="hr.sales.tes@x")
    assert client.post("/periods/close", json={"tahun": tahun, "bulan": bulan, "alasan": "uji"}).status_code == 200

    login("Sales/Marketing")
    r = client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())})
    assert r.status_code == 200 and r.json()["can_save"]
    c = client.post(f"/sales/{r.json()['upload_id']}/confirm")
    assert c.status_code == 409 and "ditutup" in c.json()["detail"]

    # Setelah dibuka kembali, unggah boleh
    login("HR", email="hr.sales.tes@x")
    assert client.post("/periods/reopen", json={"tahun": tahun, "bulan": bulan, "alasan": "uji"}).status_code == 200
    login("Sales/Marketing")
    assert upload_ok(client)["inserted_performance"] == 2


def test_quarter_and_year_aggregation(client):
    """PRD F31/F35: capaian kuartal dan tahun menjumlahkan bulan-bulan yang tercakup."""
    login("Sales/Marketing")

    def month_xlsx(periode: str, penjualan: float, target_cabang: float, target_perusahaan: float) -> bytes:
        wb = openpyxl.Workbook()
        ws = wb.active; ws.title = "Penjualan"
        ws.append(["employee_id", "nama", "cabang", "periode", "penjualan", "penerimaan", "piutang_outlet", "sisa_bad_debt"])
        ws.append(["S001", "Andi", "Bandung", periode, penjualan, 0, 0, 0])
        wt = wb.create_sheet("Target")
        wt.append(["level", "cabang", "employee_id", "periode", "target_penjualan"])
        wt.append(["Perusahaan", None, None, periode, target_perusahaan])
        wt.append(["Cabang", "Bandung", None, periode, target_cabang])
        wt.append(["Salesman", "Bandung", "S001", periode, target_cabang])
        buf = io.BytesIO(); wb.save(buf); return buf.getvalue()

    def upload(data: bytes):
        r = client.post("/sales/preview", files={"file": ("p.xlsx", data)})
        assert r.status_code == 200, r.text
        c = client.post(f"/sales/{r.json()['upload_id']}/confirm")
        assert c.status_code == 200, c.text

    # Q3 2026: Juli 100jt/100jt, Agustus 150jt/100jt, September 50jt/100jt -> total 300jt / target 300jt
    upload(month_xlsx("2026-07", 100_000_000, 100_000_000, 300_000_000))
    upload(month_xlsx("2026-08", 150_000_000, 100_000_000, 300_000_000))
    upload(month_xlsx("2026-09", 50_000_000, 100_000_000, 300_000_000))

    login("HR", email="hr.sales.tes@x")
    q = client.get("/sales/cabang", params={"periode": "2026-Q3", "granularitas": "kuartal"}).json()
    assert len(q) == 1
    row = q[0]
    assert row["total_penjualan"] == 300_000_000 and row["target_cabang"] == 300_000_000
    assert round(row["capaian_pct"]) == 100 and row["status"] == "Tercapai"
    assert row["granularitas"] == "kuartal"

    # Perusahaan kuartal
    p = client.get("/sales/perusahaan", params={"periode": "2026-Q3", "granularitas": "kuartal"}).json()
    assert p["total_penjualan"] == 300_000_000 and p["target_perusahaan"] == 900_000_000
    assert round(p["capaian_pct"], 1) == 33.3 and p["cabang_tercapai"] == 1

    # Tahun 2026 menjumlahkan 12 bulan (hanya 3 bulan berisi data)
    y = client.get("/sales/perusahaan", params={"periode": "2026", "granularitas": "tahun"}).json()
    assert y["total_penjualan"] == 300_000_000 and y["granularitas"] == "tahun"

    # Format periode tidak valid ditolak
    assert client.get("/sales/cabang", params={"periode": "2026-13", "granularitas": "kuartal"}).status_code == 422
    assert client.get("/sales/perusahaan", params={"periode": "26", "granularitas": "tahun"}).status_code == 422


def test_cabang_lists_salesmen_not_eligible(client):
    """PRD F35: daftar nama salesman belum layak per cabang."""
    login("Sales/Marketing")
    upload_ok(client)   # S001 layak (150jt >= 100jt), S002 tidak layak (80jt < 100jt)
    login("HR", email="hr.sales.tes@x")
    row = client.get("/sales/cabang", params={"periode": P}).json()[0]
    assert row["salesman_belum_layak"] == ["Budi"]
    assert row["salesman_tidak_layak"] == 1 and row["salesman_layak"] == 1


def test_access(client):
    login("HR", email="hr.sales.tes@x")
    assert client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())}).status_code == 403
    login("Audit", email="aud.sales.tes@x")
    assert client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())}).status_code == 403
    assert client.get("/sales/salesman", params={"periode": P}).status_code == 200
    login("IT", email="it.sales.tes@x")
    assert client.get("/sales/salesman", params={"periode": P}).status_code == 403
    app.dependency_overrides.clear()
    assert client.get("/sales/template").status_code == 401   # template butuh login


def test_approval(client):
    login("Sales/Marketing")
    upload_ok(client)
    url = f"/sales/assessment/S002/{P}"
    body = {"status": "Disetujui", "notes": "Disetujui meski di bawah target"}
    assert client.patch(url, json=body).status_code == 403     # Sales/Marketing biasa
    login("HR", email="hr.sales.tes@x")
    assert client.patch(url, json=body).status_code == 403
    login("Sales/Marketing", approver=True, email="appr.sales.tes@x")
    assert client.patch(url, json=body).status_code == 200
    assert client.patch(f"/sales/assessment/S999/{P}", json=body).status_code == 404
    row = [r for r in client.get("/sales/salesman", params={"periode": P}).json() if r["employee_id"] == "S002"][0]
    assert row["assessment_status"] == "Disetujui" and row["approved_by"] == "Akun Tes"
    assert row["catatan"] == "Disetujui meski di bawah target"


def test_meta(client):
    login("Sales/Marketing")
    upload_ok(client)
    login("Audit", email="aud.sales.tes@x")
    assert client.get("/sales/meta").json() == {"periodes": [P], "cabang": ["Bandung"]}
    login("IT", email="it.sales.tes@x")
    assert client.get("/sales/meta").status_code == 403


# ---------- Unggah ulang periode (PRD bagian 13, pertanyaan 19 dan 20) ----------

def _xlsx(rows_pen, rows_tar) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Penjualan"
    ws.append(["employee_id", "nama", "cabang", "periode", "penjualan", "penerimaan", "piutang_outlet", "sisa_bad_debt"])
    for r in rows_pen:
        ws.append(r)
    wt = wb.create_sheet("Target")
    wt.append(["level", "cabang", "employee_id", "periode", "target_penjualan"])
    for r in rows_tar:
        wt.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _preview(client, data):
    r = client.post("/sales/preview", files={"file": ("p.xlsx", data)})
    assert r.status_code == 200, r.text
    assert r.json()["can_save"] is True, r.json()
    return r.json()


def _decide(client, emp, status, notes=None):
    body = {"status": status}
    if notes:
        body["notes"] = notes
    return client.patch(f"/sales/assessment/{emp}/{P}", json=body)


def test_approval_note_required_when_differs_from_recommendation(client):
    login("Sales/Marketing")
    upload_ok(client)
    login("Sales/Marketing", approver=True, email="appr.tes@x")
    # S002 Tidak Layak: menyetujui tanpa catatan ditolak, dengan catatan berhasil
    assert _decide(client, "S002", "Disetujui").status_code == 422
    assert _decide(client, "S002", "Disetujui", "kendala stok").status_code == 200
    # S001 Layak: menyetujui sesuai rekomendasi tanpa catatan boleh
    assert _decide(client, "S001", "Disetujui").status_code == 200
    # kembali ke Draf membersihkan nama penyetuju
    assert _decide(client, "S002", "Draf").status_code == 200
    login("HR", email="hr.sales.tes@x")
    rows = {r["employee_id"]: r for r in client.get("/sales/salesman", params={"periode": P}).json()}
    assert rows["S002"]["assessment_status"] == "Draf" and rows["S002"]["approved_by"] is None


def test_reupload_requires_confirmation_and_resets_to_draft(client):
    login("Sales/Marketing")
    upload_ok(client)
    login("Sales/Marketing", approver=True, email="appr.tes@x")
    assert _decide(client, "S001", "Disetujui").status_code == 200

    # file yang sama diunggah ulang: preview memberi tahu, confirm tanpa timpa ditolak 409
    p = _preview(client, sales_xlsx())
    assert p["perlu_konfirmasi"] is True and p["reupload"][0]["penilaian_diputuskan"] == 1
    assert client.post(f"/sales/{p['upload_id']}/confirm").status_code == 409

    # data belum berubah setelah ditolak
    login("HR", email="hr.sales.tes@x")
    rows = {r["employee_id"]: r for r in client.get("/sales/salesman", params={"periode": P}).json()}
    assert rows["S001"]["assessment_status"] == "Disetujui"

    login("Sales/Marketing", email="sales.tes@x")
    c = client.post(f"/sales/{p['upload_id']}/confirm", params={"timpa": "true"})
    assert c.status_code == 200, c.text
    assert c.json()["assessments_reset"] == 1
    login("HR", email="hr.sales.tes@x")
    rows = {r["employee_id"]: r for r in client.get("/sales/salesman", params={"periode": P}).json()}
    assert rows["S001"]["assessment_status"] == "Draf" and rows["S001"]["approved_by"] is None


def test_reupload_only_replaces_cabang_in_file_and_recomputes_recommendation(client):
    login("Sales/Marketing")
    upload_ok(client)  # Bandung: S001 (target 100jt, jual 150jt), S002
    # tambah cabang Jakarta lewat file terpisah, periode sama
    jkt = _xlsx(
        [("S010", "Cici", "Jakarta", P, 50_000_000, None, None, None)],
        [("Cabang", "Jakarta", None, P, 60_000_000), ("Salesman", "Jakarta", "S010", P, 50_000_000)],
    )
    p = _preview(client, jkt)
    assert p["perlu_konfirmasi"] is False and p["reupload"] == []
    assert client.post(f"/sales/{p['upload_id']}/confirm").status_code == 200

    login("Sales/Marketing", approver=True, email="appr.tes@x")
    assert _decide(client, "S001", "Disetujui").status_code == 200  # Bandung sudah diputuskan

    # unggah ulang Jakarta saja: Bandung tidak boleh berubah, rekomendasi S010 dihitung ulang
    jkt2 = _xlsx(
        [("S010", "Cici", "Jakarta", P, 40_000_000, None, None, None)],
        [("Cabang", "Jakarta", None, P, 60_000_000), ("Salesman", "Jakarta", "S010", P, 50_000_000)],
    )
    p = _preview(client, jkt2)
    assert p["perlu_konfirmasi"] is False  # tidak ada penilaian diputuskan di cakupan Jakarta
    assert client.post(f"/sales/{p['upload_id']}/confirm").status_code == 200

    login("HR", email="hr.sales.tes@x")
    rows = {r["employee_id"]: r for r in client.get("/sales/salesman", params={"periode": P}).json()}
    assert set(rows) == {"S001", "S002", "S010"}
    assert rows["S001"]["assessment_status"] == "Disetujui"          # Bandung utuh
    assert rows["S010"]["status_kelayakan"] == "Tidak Layak"          # 40jt < target 50jt
    per = client.get("/sales/perusahaan", params={"periode": P}).json()
    assert per["target_perusahaan"] == 500_000_000                    # target perusahaan tidak ikut terhapus


def test_reupload_removes_assessment_of_salesman_missing_from_new_file(client):
    login("Sales/Marketing")
    upload_ok(client)
    login("Sales/Marketing", approver=True, email="appr.tes@x")
    assert _decide(client, "S002", "Ditolak").status_code == 200
    only_s001 = _xlsx(
        [("S001", "Andi", "Bandung", P, 150_000_000, None, None, None)],
        [("Cabang", "Bandung", None, P, 250_000_000), ("Salesman", "Bandung", "S001", P, 100_000_000)],
    )
    p = _preview(client, only_s001)
    assert p["perlu_konfirmasi"] is True
    c = client.post(f"/sales/{p['upload_id']}/confirm", params={"timpa": "true"})
    assert c.status_code == 200 and c.json()["assessments_removed"] == 1
    login("HR", email="hr.sales.tes@x")
    ids = {r["employee_id"] for r in client.get("/sales/salesman", params={"periode": P}).json()}
    assert ids == {"S001"}


# ---------- Kehadiran salesman dari data presensi (PRD F31) ----------

def _presensi_csv(rows) -> bytes:
    head = "nik,nama,tanggal,jam_masuk,jam_keluar,status,departemen\n"
    return (head + "\n".join(rows) + "\n").encode()


def _upload_presensi(client, csv: bytes):
    login("HR", email="hr.sales.tes@x")
    r = client.post("/uploads/preview", files={"file": ("presensi.csv", csv, "text/csv")})
    assert r.status_code == 200 and r.json()["can_save"] is True, r.text
    assert client.post(f"/uploads/{r.json()['upload_id']}/confirm").status_code == 200


def test_salesman_attendance_and_missing_presensi_warning(client):
    # Presensi dulu: S001 hadir 3 hari kerja (1 terlambat), S002 tidak ada di presensi.
    # 2026-10-05 sampai 07 adalah Senin sampai Rabu (hari kerja).
    _upload_presensi(client, _presensi_csv([
        "S001,Andi,2026-10-05,07:55,17:00,Hadir,Sales/Marketing",
        "S001,Andi,2026-10-06,08:30,17:00,Hadir,Sales/Marketing",
        "S001,Andi,2026-10-07,,,Alpa,Sales/Marketing",
    ]))
    login("Sales/Marketing")
    r = client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())})
    assert r.status_code == 200, r.text
    warns = " ".join(r.json()["warnings"])
    assert "1 salesman periode 2026-10 tidak ditemukan di data presensi (S002)" in warns
    assert client.post(f"/sales/{r.json()['upload_id']}/confirm").status_code == 200

    login("HR", email="hr.sales.tes@x")
    rows = {x["employee_id"]: x for x in client.get("/sales/salesman", params={"periode": P}).json()}
    assert (rows["S001"]["hari_kerja"], rows["S001"]["hari_hadir"], rows["S001"]["terlambat_hari"]) == (3, 2, 1)
    assert rows["S001"]["kehadiran_pct"] == 66.7
    # tanpa data presensi: kosong, bukan nol
    assert rows["S002"]["kehadiran_pct"] is None and rows["S002"]["hari_kerja"] is None


def test_no_presensi_for_period_gives_single_warning(client):
    login("Sales/Marketing")
    r = client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())})
    warns = r.json()["warnings"]
    assert any("Data presensi periode 2026-10 belum ada" in w for w in warns)


def test_warning_when_salesman_nik_belongs_to_other_department(client):
    # S001 di presensi tercatat sebagai karyawan Audit, S002 benar-benar Sales/Marketing
    _upload_presensi(client, _presensi_csv([
        "S001,Fajar,2026-10-05,07:55,17:00,Hadir,Audit",
        "S002,Budi,2026-10-05,07:55,17:00,Hadir,Sales/Marketing",
    ]))
    login("Sales/Marketing")
    r = client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())})
    assert r.status_code == 200 and r.json()["can_save"] is True   # peringatan, bukan penghalang
    warns = " ".join(r.json()["warnings"])
    assert "1 salesman periode 2026-10 tercatat di departemen presensi selain Sales/Marketing" in warns
    assert "S001 (Audit)" in warns and "S002 (" not in warns


def test_no_department_warning_when_all_salesmen_in_sales(client):
    _upload_presensi(client, _presensi_csv([
        "S001,Andi,2026-10-05,07:55,17:00,Hadir,Sales/Marketing",
        "S002,Budi,2026-10-05,07:55,17:00,Hadir,Sales/Marketing",
    ]))
    login("Sales/Marketing")
    r = client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())})
    assert not any("selain Sales/Marketing" in w for w in r.json()["warnings"])


# ---------- Selisih antar tingkat target (PRD F33) ----------

def _warns(client, data):
    login("Sales/Marketing")
    r = client.post("/sales/preview", files={"file": ("p.xlsx", data)})
    assert r.status_code == 200 and r.json()["can_save"] is True, r.text   # selisih hanya peringatan
    return r.json()["warnings"]


def test_target_levels_mismatch_is_flagged(client):
    # sales_xlsx: salesman Bandung 100jt + 100jt = 200jt, target cabang 250jt, target perusahaan 500jt
    w = " ".join(_warns(client, sales_xlsx()))
    assert "Jumlah target salesman tidak sama dengan target cabang" in w
    assert "2026-10 Bandung: salesman Rp200.000.000, cabang Rp250.000.000" in w
    assert "Jumlah target cabang di file ini tidak sama dengan target perusahaan" in w
    assert "2026-10: cabang Rp250.000.000, perusahaan Rp500.000.000" in w


def test_target_levels_consistent_has_no_mismatch_warning(client):
    ok = _xlsx(
        [("S001", "Andi", "Bandung", P, 150_000_000, None, None, None), ("S002", "Budi", "Bandung", P, 80_000_000, None, None, None)],
        [("Perusahaan", None, None, P, 250_000_000), ("Cabang", "Bandung", None, P, 250_000_000),
         ("Salesman", "Bandung", "S001", P, 150_000_000), ("Salesman", "Bandung", "S002", P, 100_000_000)],
    )
    w = " ".join(_warns(client, ok))
    assert "tidak sama dengan target" not in w


def test_target_cabang_missing_from_penjualan_is_flagged(client):
    data = _xlsx(
        [("S001", "Andi", "Bandung", P, 150_000_000, None, None, None)],
        [("Perusahaan", None, None, P, 300_000_000), ("Cabang", "Bandung", None, P, 150_000_000),
         ("Cabang", "Medan", None, P, 150_000_000), ("Salesman", "Bandung", "S001", P, 150_000_000)],
    )
    w = " ".join(_warns(client, data))
    assert "1 cabang di sheet Target tidak ada di sheet Penjualan pada periode yang sama: Medan (2026-10)" in w


def test_department_warning_accepts_configured_sales_org_names(client):
    # "Sales" dan "Marketing" dikenali sebagai departemen Sales (settings.sales_org_names)
    _upload_presensi(client, _presensi_csv([
        "S001,Andi,2026-10-05,07:55,17:00,Hadir,Sales",
        "S002,Budi,2026-10-05,07:55,17:00,Hadir,Marketing",
    ]))
    login("Sales/Marketing")
    r = client.post("/sales/preview", files={"file": ("p.xlsx", sales_xlsx())})
    assert not any("selain Sales/Marketing" in w for w in r.json()["warnings"])

# ---------- Regresi: hapus upload penjualan harus membersihkan seluruh data ----------

def test_delete_sales_upload_clears_all_sales_data(client):
    """Bug lama: menghapus upload tidak membersihkan sales_performance/targets/assessments,
    sehingga data penjualan tetap tampil walau upload-nya sudah dihapus (data yatim)."""
    login("Sales/Marketing")
    res = upload_ok(client)
    upload_id = res["upload"]["id"]

    with SessionLocal() as db:
        assert db.query(SalesPerformance).count() == 2
        assert db.query(SalesTarget).count() == 4
        assert db.query(SalesAssessment).count() == 2

    # Endpoint daftar upload penjualan menampilkan paket ini.
    lst = client.get("/sales/uploads")
    assert lst.status_code == 200
    assert any(u["id"] == upload_id for u in lst.json())

    r = client.delete(f"/sales/uploads/{upload_id}")
    assert r.status_code == 200, r.text

    with SessionLocal() as db:
        assert db.query(SalesPerformance).count() == 0
        assert db.query(SalesTarget).count() == 0
        assert db.query(SalesAssessment).count() == 0

    # Dashboard tidak lagi menampilkan data periode itu.
    assert client.get("/sales/salesman", params={"periode": P}).json() == []
    assert client.get("/sales/uploads").json() == []


def test_delete_sales_upload_blocked_when_period_closed(client):
    """Periode tertutup tidak boleh diubah lewat hapus upload penjualan (PRD F13)."""
    from app.services import period as period_svc

    login("Sales/Marketing")
    res = upload_ok(client)
    upload_id = res["upload"]["id"]
    tahun, bulan = (int(x) for x in P.split("-"))
    with SessionLocal() as db:
        period_svc.close(db, tahun, bulan, 1, "uji")
        db.commit()
    try:
        assert client.delete(f"/sales/uploads/{upload_id}").status_code == 409
        with SessionLocal() as db:
            assert db.query(SalesPerformance).count() == 2   # masih utuh
    finally:
        with SessionLocal() as db:
            period_svc.reopen(db, tahun, bulan, 1, "buka lagi")
            db.commit()


def test_delete_presensi_upload_also_clears_sales_rows(client):
    """Hapus paket presensi yang upload_id-nya juga punya data penjualan harus ikut bersih."""
    from app.services.deletion import delete_upload_rows
    from app.models import Upload

    login("Sales/Marketing")
    res = upload_ok(client)
    upload_id = res["upload"]["id"]

    with SessionLocal() as db:
        up = db.get(Upload, upload_id)
        delete_upload_rows(db, [up], 1, "uji")
        db.commit()
        assert db.query(SalesPerformance).count() == 0
        assert db.query(SalesTarget).count() == 0
        assert db.query(SalesAssessment).count() == 0
