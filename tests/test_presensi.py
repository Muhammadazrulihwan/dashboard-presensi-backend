import io
from datetime import datetime, time

import polars as pl
import xlsxwriter

from app.db import SessionLocal
from tests.conftest import as_dept

# 2026-10-05 adalah hari Senin, 10-11 Minggu. Hitungan manual:
#   Ani 10-05 07:55-17:10 -> terlambat 0, lembur 10, durasi 9,25
#   Ani 10-06 08:10-16:50 -> terlambat 10, pulang cepat 10, durasi 8,67
#   Budi 10-05 08:30-17:00 -> terlambat 30 ; Budi 10-08 08:00-18:30 -> lembur 90
HEADER = "nik,nama,tanggal,departemen,shift,jam_masuk,jam_keluar,status,keterangan\n"
CSV_OK = HEADER + "\n".join([
    "001,Ani,2026-10-05,IT,,07:55,17:10,Hadir,",
    "001,Ani,2026-10-06,IT,,08:10,16:50,Hadir,",
    "001,Ani,2026-10-07,IT,,,,Izin,Urusan keluarga",
    "001,Ani,2026-10-08,IT,,,,,",
    "001,Ani,2026-10-09,IT,,08:00,17:00,Hadir,",
    "001,Ani,2026-10-11,IT,,,,,",
    "002,Budi,2026-10-05,HR,,08:30,17:00,Hadir,",
    "002,Budi,2026-10-06,HR,,,,Sakit,Demam",
    "002,Budi,2026-10-07,HR,,,,Dinas Luar,",
    "002,Budi,2026-10-08,HR,,08:00,18:30,Hadir,",
    "002,Budi,2026-10-09,HR,,,,Cuti,Cuti tahunan",
]) + "\n"
RANGE = {"from": "2026-10-05", "to": "2026-10-09"}


def upload(client, content=CSV_OK, name="presensi.csv", mode=None, confirm=True):
    r = client.post("/uploads/preview", files={"file": (name, content, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    if confirm and body["can_save"]:
        c = client.post(f"/uploads/{body['upload_id']}/confirm", json={"mode": mode} if mode else None)
        assert c.status_code == 200, c.text
        return body, c.json()
    return body, None


def rows(client, **params):
    p = {"page_size": 100, **params}
    return client.get("/dashboard/records", params=p).json()["items"]


def test_template_and_health(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/uploads/template")
    assert r.status_code == 200 and r.content[:2] == b"PK"
    df = pl.read_excel(io.BytesIO(r.content), sheet_name="Presensi", engine="calamine")
    assert df.columns[:3] == ["nik", "nama", "tanggal"]


def test_calculation_matches_manual(client):
    body, saved = upload(client)
    assert body["can_save"] and body["row_count"] == 11 and body["conflict_count"] == 0
    assert body["employee_count"] == 2 and body["date_from"] == "2026-10-05" and body["date_to"] == "2026-10-11"
    assert saved["inserted"] == 11 and saved["upload"]["status"] == "saved"
    by = {(r["nik"], r["tanggal"]): r for r in rows(client)}
    a05, a06 = by[("001", "2026-10-05")], by[("001", "2026-10-06")]
    assert (a05["menit_terlambat"], a05["durasi_kerja"], a05["jam_masuk"], a05["jam_keluar"]) == (0, 9.25, "07:55", "17:10")
    assert (a06["menit_terlambat"], a06["durasi_kerja"]) == (10, 8.67)
    assert by[("002", "2026-10-05")]["menit_terlambat"] == 30
    assert by[("001", "2026-10-08")]["status"] == "Alpa"      # kosong tanpa jam di hari kerja
    assert by[("001", "2026-10-11")]["status"] == "Libur"     # Minggu tidak dihitung alpa
    assert by[("001", "2026-10-07")]["keterangan"] == "Urusan keluarga"


def test_summary_kpi_and_composition(client):
    upload(client)
    s = client.get("/dashboard/summary", params=RANGE).json()
    cur = s["current"]
    assert cur["hari_kerja"] == 10 and cur["hadir"] == 6 and cur["kehadiran_pct"] == 60.0
    assert (cur["terlambat"], cur["menit_terlambat"]) == (2, 40)
    assert (cur["izin"], cur["sakit"], cur["cuti"], cur["alpa"], cur["dinas_luar"], cur["tidak_hadir"]) == (1, 1, 1, 1, 1, 4)
    assert cur["avg_jam_masuk"] == "08:07" and cur["karyawan"] == 2
    assert s["previous"]["hari_kerja"] == 0 and s["previous"]["kehadiran_pct"] is None   # periode sebelumnya kosong
    comp = {c["status"]: c["total"] for c in client.get("/dashboard/status-composition", params=RANGE).json()}
    assert comp == {"Hadir": 3, "Terlambat": 2, "Izin": 1, "Sakit": 1, "Cuti": 1, "Alpa": 1, "Dinas Luar": 1}


def test_previous_period_comparison(client):
    upload(client)
    s = client.get("/dashboard/summary", params={"from": "2026-10-10", "to": "2026-10-14"}).json()
    assert s["previous"]["hari_kerja"] == 10  # 05 sampai 09 Oktober


def test_trend_department_top_and_filters(client):
    upload(client)
    day = client.get("/dashboard/trend", params={**RANGE, "granularity": "day"}).json()
    assert [d["period"] for d in day][0] == "2026-10-05" and day[0]["kehadiran_pct"] == 100.0 and day[0]["terlambat"] == 1
    month = client.get("/dashboard/trend", params={"granularity": "month"}).json()
    assert month == [{"period": "2026-10", "kehadiran_pct": 60.0, "terlambat": 2}]
    dept = {d["departemen"]: d for d in client.get("/dashboard/by-department", params=RANGE).json()}
    assert dept["IT"]["kehadiran_pct"] == 60.0 and dept["HR"]["terlambat"] == 1 and dept["HR"]["menit_terlambat"] == 30
    top = client.get("/dashboard/top-employees", params={**RANGE, "metric": "terlambat"}).json()
    assert [t["nik"] for t in top] == ["002", "001"] or top[0]["terlambat"] == 1
    assert client.get("/dashboard/top-employees", params={**RANGE, "metric": "alpa"}).json()[0]["nik"] == "001"
    assert client.get("/dashboard/summary", params={**RANGE, "departemen": "IT"}).json()["current"]["karyawan"] == 1
    assert client.get("/dashboard/summary", params={**RANGE, "status": "Terlambat"}).json()["current"]["records"] == 2
    assert client.get("/dashboard/summary", params={**RANGE, "karyawan": "bud"}).json()["current"]["karyawan"] == 1
    assert client.get("/dashboard/summary", params={"from": "2026-13-01"}).status_code == 422
    assert client.get("/dashboard/summary", params={"status": "Bolos"}).status_code == 422
    m = client.get("/dashboard/meta").json()
    assert m["min_date"] == "2026-10-05" and m["max_date"] == "2026-10-11" and m["departemen"] == ["HR", "IT"]


def test_employee_detail(client):
    upload(client)
    d = client.get("/dashboard/employee/001", params=RANGE).json()
    assert d["nik"] == "001" and d["nama"] == "Ani" and d["departemen"] == "IT"
    assert d["rekap"]["karyawan"] == 1 and d["rekap"]["hadir"] == 3 and d["rekap"]["terlambat"] == 1
    assert [r["tanggal"] for r in d["range"]] == ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
    assert d["trend"][0]["kehadiran_pct"] == 100.0 and d["trend"][0]["terlambat"] == 0
    assert client.get("/dashboard/employee/999", params=RANGE).status_code == 404
    assert len(client.get("/dashboard/employee/001", params={**RANGE, "status": "Hadir"}).json()["range"]) == 3


def test_employee_detail_for_performance_departments(client):
    upload(client)
    for dept in ("HR", "Audit"):  # HR dan Audit melihat semua karyawan
        as_dept(dept)
        assert client.get("/dashboard/employee/002", params=RANGE).json()["nik"] == "002"
        assert client.get("/dashboard/employee/001", params=RANGE).json()["nik"] == "001"
    as_dept("Sales/Marketing")  # data uji ini berisi karyawan IT dan HR, di luar batas Sales/Marketing
    assert client.get("/dashboard/employee/001", params=RANGE).status_code == 404
    as_dept("IT")
    assert client.get("/dashboard/employee/001", params=RANGE).status_code == 403      # IT tidak menilai kinerja


def test_employee_pdf_export(client):
    upload(client)
    one = client.get("/exports/employee/pdf", params={**RANGE, "nik": "001"})
    assert one.status_code == 200 and one.content[:4] == b"%PDF"
    assert "rekap_karyawan_001.pdf" in one.headers["content-disposition"]
    allr = client.get("/exports/employee/pdf", params=RANGE)
    assert allr.status_code == 200 and allr.content[:4] == b"%PDF"
    assert len(allr.content) > len(one.content)   # dua karyawan, dua halaman
    assert client.get("/exports/employee/pdf", params={**RANGE, "nik": "999"}).status_code == 404
    as_dept("Audit")
    assert client.get("/exports/employee/pdf", params={**RANGE, "nik": "002"}).status_code == 200
    as_dept("Sales/Marketing")
    assert client.get("/exports/employee/pdf", params={**RANGE, "nik": "002"}).status_code == 404   # di luar batas
    as_dept("IT")
    assert client.get("/exports/employee/pdf", params=RANGE).status_code == 403   # IT tidak menilai kinerja


def test_records_sort_search_paging(client):
    upload(client)
    p = client.get("/dashboard/records", params={"page_size": 3, "sort": "menit_terlambat", "order": "desc"}).json()
    assert p["total"] == 11 and len(p["items"]) == 3 and p["items"][0]["menit_terlambat"] == 30
    assert client.get("/dashboard/records", params={"q": "ani"}).json()["total"] == 6
    assert client.get("/dashboard/records", params={"q": "002"}).json()["total"] == 5


def test_validation_errors(client):
    bad = HEADER + "\n".join([
        "001,Ani,2026-10-05,IT,,08:00,17:00,Hadir,",
        "001,Ani,2026-10-05,IT,,08:00,17:00,Hadir,",   # duplikat
        "002,Budi,05/10/2026,HR,,08:00,17:00,Hadir,",   # tanggal salah
        "003,Cici,2026-10-06,HR,,25:00,17:00,Hadir,",   # jam salah
        "004,Dedi,2026-10-06,HR,,17:00,08:00,Hadir,",   # keluar lebih awal
        "005,Eko,2026-10-06,HR,,,,Izin,",               # tanpa keterangan
        "006,Fani,2026-10-06,HR,,,,Bolos,",             # status salah
        "007,Gina,2026-10-06,HR,,08:00,,Hadir,",        # jam keluar kosong
        "008,Hadi,2026-10-06,HR,XYZ,08:00,17:00,Hadir,",  # shift tak dikenal
    ]) + "\n"
    body, _ = upload(client, bad)
    assert not body["can_save"] and body["error_count"] == 8
    got = {(e["row"], e["column"]): e["message"] for e in body["errors"]}
    assert "Duplikat" in got[(3, "nik")] and "YYYY-MM-DD" in got[(4, "tanggal")] and "HH:MM" in got[(5, "jam_masuk")]
    assert "lebih awal" in got[(6, "jam_keluar")] and "Keterangan wajib" in got[(7, "keterangan")]
    assert "Status tidak valid" in got[(8, "status")] and "Jam keluar wajib" in got[(9, "jam_keluar")] and "Kode shift" in got[(10, "shift")]
    assert client.get("/dashboard/summary").json()["current"]["records"] == 0


def test_missing_columns_and_aliases(client):
    body, _ = upload(client, "nik,nama,tanggal,status\n001,Ani,2026-10-05,Hadir\n", confirm=False)
    assert body["error_count"] == 1 and "jam_masuk" in body["errors"][0]["column"]
    mesin = "NIP;Nama Karyawan;Tgl;Divisi;Check In;Check Out;Kehadiran\n001;Ani;2026-10-05;IT;08:00;17:00;Hadir\n"
    body, _ = upload(client, mesin.encode("utf-8-sig"))
    assert body["can_save"] and any("NIP" in m for m in body["mapped_columns"])


def test_wrong_extension_and_size(client, monkeypatch):
    assert client.post("/uploads/preview", files={"file": ("x.txt", b"abc", "text/plain")}).status_code == 400
    from app.core.config import settings
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    assert client.post("/uploads/preview", files={"file": ("p.csv", CSV_OK, "text/csv")}).status_code == 413


def test_conflict_overwrite_and_skip(client):
    upload(client)
    revisi = HEADER + "001,Ani,2026-10-05,IT,,09:00,17:00,Hadir,\n001,Ani,2026-10-12,IT,,08:00,17:00,Hadir,\n"
    body, _ = upload(client, revisi, confirm=False)
    assert body["conflict_count"] == 1
    r = client.post(f"/uploads/{body['upload_id']}/confirm")
    assert r.status_code == 409 and "Timpa atau Lewati" in r.json()["detail"]
    r = client.post(f"/uploads/{body['upload_id']}/confirm", json={"mode": "overwrite"}).json()
    assert (r["inserted"], r["overwritten"], r["skipped"]) == (2, 1, 0)
    by = {(x["nik"], x["tanggal"]): x for x in rows(client)}
    assert by[("001", "2026-10-05")]["menit_terlambat"] == 60 and len(by) == 12

    body2, _ = upload(client, HEADER + "001,Ani,2026-10-05,IT,,07:00,17:00,Hadir,\n", confirm=False)
    r = client.post(f"/uploads/{body2['upload_id']}/confirm", json={"mode": "skip"}).json()
    assert (r["inserted"], r["skipped"]) == (0, 1)
    assert {(x["nik"], x["tanggal"]): x for x in rows(client)}[("001", "2026-10-05")]["menit_terlambat"] == 60


def test_overnight_shift(client):
    assert client.put("/settings/shifts/malam", json={"jam_masuk": "22:00", "jam_pulang": "06:00", "toleransi_menit": 0}).json()["lintas_hari"] is True
    csv = HEADER + "003,Candra,2026-10-05,Produksi,MALAM,22:05,06:10,Hadir,\n"
    body, _ = upload(client, csv)
    assert body["can_save"], body["errors"]
    r = rows(client)[0]
    assert (r["menit_terlambat"], r["durasi_kerja"]) == (5, 8.08)
    assert client.delete("/settings/shifts/MALAM").status_code == 409   # masih dipakai data


def test_recalculate_after_rule_change(client):
    upload(client)
    client.put("/settings/shifts/REGULER", json={"jam_masuk": "08:00", "jam_pulang": "17:00", "toleransi_menit": 15})
    assert client.post("/settings/recalculate", json={"from": "2026-10-01", "to": "2026-10-31"}).json()["updated"] == 11
    by = {(x["nik"], x["tanggal"]): x for x in rows(client)}
    assert by[("001", "2026-10-06")]["menit_terlambat"] == 0       # 10 menit masih dalam toleransi
    assert by[("002", "2026-10-05")]["menit_terlambat"] == 15      # 30 - 15
    client.post("/settings/holidays", json={"tanggal": "2026-10-06", "nama": "Libur uji"})
    client.post("/settings/recalculate", json={"from": "2026-10-01", "to": "2026-10-31"})
    s = client.get("/dashboard/summary", params=RANGE).json()["current"]
    assert s["hari_kerja"] == 8 and s["sakit"] == 0    # 06 Oktober tidak lagi hari kerja
    assert client.post("/settings/holidays", json={"tanggal": "2026-10-06", "nama": "Ganda"}).status_code == 409
    assert [h["nama"] for h in client.get("/settings/holidays", params={"year": 2026}).json()] == ["Libur uji"]


def test_xlsx_with_time_cells_and_numeric_nik(client):
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet()
    ws.write_row(0, 0, ["nik", "nama", "tanggal", "jam_masuk", "jam_keluar", "status"])
    tf, df = wb.add_format({"num_format": "hh:mm"}), wb.add_format({"num_format": "yyyy-mm-dd"})
    ws.write_number(1, 0, 1234)
    ws.write(1, 1, "Dewi")
    ws.write_datetime(1, 2, datetime(2026, 10, 5), df)
    ws.write_datetime(1, 3, datetime(1899, 12, 31, 8, 3), tf) if False else ws.write_number(1, 3, (8 * 60 + 3) / 1440, tf)
    ws.write_number(1, 4, (17 * 60) / 1440, tf)
    ws.write(1, 5, "Hadir")
    wb.close()
    r = client.post("/uploads/preview", files={"file": ("d.xlsx", buf.getvalue(), "application/octet-stream")})
    body = r.json()
    assert body["can_save"], body
    client.post(f"/uploads/{body['upload_id']}/confirm")
    row = rows(client)[0]
    assert row["nik"] == "1234" and row["jam_masuk"] == "08:03" and row["menit_terlambat"] == 3


def test_delete_upload(client):
    body, _ = upload(client)
    r = client.request("DELETE", f"/uploads/{body['upload_id']}", json={"alasan": "salah unggah"})
    assert r.status_code == 200 and r.json()["status"] == "deleted"
    assert client.get("/dashboard/summary").json()["current"]["records"] == 0
    assert client.delete(f"/uploads/{body['upload_id']}").status_code == 404
    tabs = {s: len(client.get("/uploads", params={"status": s}).json()) for s in ["saved", "failed", "deleted"]}
    assert tabs == {"saved": 0, "failed": 0, "deleted": 1}


def test_delete_upload_moves_file_to_archive_trash(client):
    """PRD F24: menghapus upload tidak memusnahkan file; arsipnya masuk tempat sampah dan bisa dipulihkan."""
    body, _ = upload(client)
    fid = client.get("/archive").json()["items"][0]["id"]

    r = client.request("DELETE", f"/uploads/{body['upload_id']}", json={"alasan": "salah unggah"})
    assert r.status_code == 200

    # Data presensi hilang dari dashboard
    assert client.get("/dashboard/summary").json()["current"]["records"] == 0
    # File asli tetap ada di arsip, statusnya tempat sampah, alasan tercatat
    aktif = client.get("/archive").json()
    assert aktif["total"] == 0
    sampah = client.get("/archive", params={"status": "sampah"}).json()
    assert sampah["total"] == 1
    item = sampah["items"][0]
    assert item["id"] == fid and item["alasan_hapus"] == "salah unggah"
    # Masih bisa diunduh dan dipulihkan
    assert client.get(f"/archive/{fid}/download").status_code == 200
    assert client.post(f"/archive/{fid}/restore").json()["status"] == "aktif"


def test_bulk_delete_uploads(client):
    """PRD F24: hapus beberapa upload sekaligus; file asli semuanya masuk tempat sampah."""
    def csv_for(nik: str, tanggal: str) -> str:
        return HEADER + f"{nik},Nama {nik},{tanggal},IT,,08:00,17:00,Hadir,\n"

    a, _ = upload(client, csv_for("101", "2026-10-05"), name="a.csv")
    b, _ = upload(client, csv_for("102", "2026-10-05"), name="b.csv")
    c, _ = upload(client, csv_for("103", "2026-10-05"), name="c.csv")
    ids = [a["upload_id"], b["upload_id"]]
    assert client.get("/dashboard/summary").json()["current"]["records"] == 3

    r = client.post("/uploads/bulk-delete", json={"ids": ids, "alasan": "duplikat"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dihapus"] == 2 and body["tidak_ditemukan"] == []
    assert body["arsip_ke_tempat_sampah"] >= 2

    # Dua terhapus dari dashboard, satu tersisa; file asli dua yang dihapus ada di tempat sampah
    assert len(client.get("/uploads", params={"status": "saved"}).json()) == 1
    assert len(client.get("/uploads", params={"status": "deleted"}).json()) == 2
    assert client.get("/dashboard/summary").json()["current"]["records"] == 1
    sampah = client.get("/archive", params={"status": "sampah"}).json()
    assert sampah["total"] == 2
    assert all(item["alasan_hapus"] == "duplikat" for item in sampah["items"])

    # ID yang sudah terhapus / tidak ada dilaporkan, bukan error (masih ada satu file valid tersisa)
    r2 = client.post("/uploads/bulk-delete", json={"ids": [a["upload_id"], 999999, c["upload_id"]]})
    assert r2.status_code == 200
    assert r2.json()["dihapus"] == 1
    assert sorted(r2.json()["tidak_ditemukan"]) == sorted([a["upload_id"], 999999])

    # Semua id tidak valid -> 404
    assert client.post("/uploads/bulk-delete", json={"ids": [999999]}).status_code == 404


def test_bulk_delete_requires_ids(client):
    assert client.post("/uploads/bulk-delete", json={"ids": []}).status_code == 422


def test_delete_upload_without_archive_still_keeps_file(client):
    """Upload lama yang belum punya arsip: file disalin ke arsip dulu, bukan dihapus permanen."""
    body, _ = upload(client)
    # Simulasikan upload lama: hapus baris arsip yang dibuat saat upload
    with SessionLocal() as db:
        from app.models import ArchivedFile, ArchivedFileEmployee
        db.query(ArchivedFileEmployee).delete()
        db.query(ArchivedFile).delete()
        db.commit()
    assert client.get("/archive").json()["total"] == 0

    r = client.delete(f"/uploads/{body['upload_id']}")
    assert r.status_code == 200
    sampah = client.get("/archive", params={"status": "sampah"}).json()
    assert sampah["total"] == 1
    assert sampah["items"][0]["checksum_terverifikasi"] is True


def test_exports(client):
    upload(client)
    x = client.get("/exports/excel", params=RANGE)
    assert x.status_code == 200 and x.content[:2] == b"PK"
    assert "rekap_presensi_2026-10-05_2026-10-09.xlsx" in x.headers["content-disposition"]
    emp = pl.read_excel(io.BytesIO(x.content), sheet_name="Rekap per Karyawan", engine="calamine")
    assert emp.height == 2 and emp["Tingkat Kehadiran"].to_list() == [0.6, 0.6]
    assert pl.read_excel(io.BytesIO(x.content), sheet_name="Rekap per Divisi", engine="calamine").height == 2
    daily = pl.read_excel(io.BytesIO(x.content), sheet_name="Data Harian", engine="calamine")
    assert daily.height == 10 and "Keterangan" in daily.columns
    p = client.get("/exports/pdf", params=RANGE)
    assert p.status_code == 200 and p.content[:4] == b"%PDF"
    empty = client.get("/exports/pdf", params={"from": "2030-01-01", "to": "2030-01-31"})
    assert empty.status_code == 200 and empty.content[:4] == b"%PDF"


def test_department_access_matrix(client):
    upload(client)
    # Sales/Marketing: dashboard + kinerja + export, tapi bukan upload/pengaturan.
    as_dept("Sales/Marketing")
    assert client.get("/dashboard/summary", params=RANGE).status_code == 200
    assert client.get("/dashboard/records").status_code == 200
    assert client.get("/exports/excel", params=RANGE).status_code == 200
    assert client.post("/uploads/preview", files={"file": ("p.csv", CSV_OK, "text/csv")}).status_code == 403
    assert client.get("/settings/shifts").status_code == 403

    # Tanpa departemen: tidak bisa membuka dashboard.
    as_dept(None)
    assert client.get("/dashboard/summary").status_code == 403

    # IT: hanya dashboard dan daftar (lihat), tidak menilai kinerja.
    as_dept("IT")
    assert client.get("/dashboard/summary", params=RANGE).status_code == 200
    assert client.get("/dashboard/by-department", params=RANGE).status_code == 200
    assert client.get("/dashboard/records").status_code == 200      # IT boleh lihat daftar
    assert client.get("/dashboard/top-employees").status_code == 403
    assert client.get("/exports/excel").status_code == 403
    assert client.get("/exports/pdf").status_code == 403


def test_arrival_vs_schedule_is_meaningful_with_multiple_shifts(client):
    # Rata-rata jam masuk bercampur antar shift (08:00 dan 22:10 menjadi sekitar 15:05), selisih dari jadwal tidak.
    assert client.put("/settings/shifts/MALAM", json={"jam_masuk": "22:00", "jam_pulang": "06:00", "toleransi_menit": 0}).status_code == 200
    csv = HEADER + "\n".join([
        "001,Ani,2026-10-05,IT,,08:00,17:00,Hadir,",          # 0 menit
        "002,Budi,2026-10-05,HR,MALAM,22:10,06:00,Hadir,",    # +10 menit
        "003,Cici,2026-10-05,HR,MALAM,21:50,06:00,Hadir,",    # -10 menit (lebih awal)
        "004,Dedi,2026-10-06,HR,MALAM,00:20,06:00,Hadir,",    # +140 menit (setelah tengah malam)
    ]) + "\n"
    body, _ = upload(client, csv)
    assert body["can_save"], body["errors"]
    cur = client.get("/dashboard/summary", params={"from": "2026-10-05", "to": "2026-10-06"}).json()["current"]
    assert cur["avg_selisih_masuk_menit"] == 35.0            # (0 + 10 - 10 + 140) / 4
    assert cur["avg_jam_masuk"] not in ("08:00", "22:10")    # rata-rata jam lama bercampur antar shift


def test_arrival_vs_schedule_basic(client):
    upload(client)
    cur = client.get("/dashboard/summary", params=RANGE).json()["current"]
    assert cur["avg_selisih_masuk_menit"] == 7.0              # (-5 + 10 + 0 + 30 + 0) / 5

