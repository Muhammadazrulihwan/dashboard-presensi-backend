"""Parser empat sumber dan Rekap Absensi terpadu (PRD F4-F9, Rilis B).

File uji dibuat mengikuti struktur kolom yang didokumentasikan PRD bagian 3, karena file contoh
asli tidak ada di repositori.
"""
import io

import openpyxl
import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Attendance, AttendanceCorrection, Employee, RawActivity, RawReport, TimeOffRequest
from tests.test_presensi import rows

# 2026-09-01 Selasa ... 2026-09-05 Sabtu (hari kerja Senin-Sabtu). 2026-09-06 Minggu libur.
P = "2026-09"


def _wb_bytes(build) -> bytes:
    wb = openpyxl.Workbook()
    build(wb)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def activity_xlsx() -> bytes:
    def build(wb):
        ws = wb.active
        ws.title = "Activity"
        ws.append(["Employee ID", "Date", "Time", "Type", "Location Setting Name", "Shift Name", "Status"])
        # LS019: 2 clock in + 2 clock out pada 09-01 -> masuk 08:20, keluar 17:05 (ambil ekstrem)
        ws.append(["LS019", "2026-09-01", "08:20", "Clock In", "Kantor", "LSP-1", "Approved"])
        ws.append(["LS019", "2026-09-01", "12:00", "Clock Out", "Kantor", "LSP-1", "Approved"])
        ws.append(["LS019", "2026-09-01", "13:00", "Clock In", "Kantor", "LSP-1", "Approved"])
        ws.append(["LS019", "2026-09-01", "17:05", "Clock Out", "Kantor", "LSP-1", "Approved"])
        # LS021: 09-02 clock in tanpa clock out -> clock out hilang
        ws.append(["LS021", "2026-09-02", "08:30", "Clock In", "Kantor", "LSP-2.", "Approved"])
        # LS417: 09-03 clock in & out normal
        ws.append(["LS417", "2026-09-03", "08:35", "Clock In", "Kantor", "LSP-1", "Approved"])
        ws.append(["LS417", "2026-09-03", "16:40", "Clock Out", "Kantor", "LSP-1", "Approved"])
    return _wb_bytes(build)


def report_xlsx() -> bytes:
    def build(wb):
        ws = wb.active
        ws.title = "Export Attendance Report"
        ws.append(["Employee ID", "Employee Name", "Date", "Shift Name", "Schedule In", "Schedule Out",
                   "Check In", "Check Out", "Late In", "Early Out", "Actual Working Hour", "Real Working Hour",
                   "Attendance Code", "Organization", "Branch"])
        ws.append(["LS019", "Kamaludin", "2026-09-01", "LSP-1", "08:30", "16:30", "08:20", "17:05",
                   "", "", "7:00", "8:45", "H", "IT", "PT Lenko - Pusat"])
        ws.append(["LS021", "Wita Aeni", "2026-09-02", "LSP-2", "08:30", "13:30", "08:30", "",
                   "", "", "5:00", "5:00", "H", "IT", "PT Lenko - Pusat"])
        ws.append(["LS417", "Berry Alpian Hidayat", "2026-09-03", "LSP-1", "08:30", "16:30", "08:35", "16:40",
                   "5", "", "7:00", "8:05", "H", "IT", "PT Lenko - Pusat"])
        # Baris subtotal yang harus dilewati
        ws.append(["TOTAL FOR EMPLOYEE : LS019 - Kamaludin", None, None, None, None, None, None, None, None, None, "19:00", None, None, None, None])
        ws.append(["GRAND TOTAL", None, None, None, None, None, None, None, None, None, "19:00", None, None, None, None])
    return _wb_bytes(build)


def timeoff_xlsx() -> bytes:
    def build(wb):
        ws = wb.active
        ws.title = "Time Off"
        ws.append(["Employee ID", "Request Id", "Start Date", "End Date", "Policy Code", "Policy Name",
                   "Time Off Type", "Duration", "Request Status"])
        ws.append(["LS021", "TO-1", "2026-09-04", "2026-09-04", "S", "Sakit", "Full Day", 1, "Approved"])
    return _wb_bytes(build)


def approval_xlsx() -> bytes:
    def build(wb):
        ws = wb.active
        ws.title = "Approval"
        ws.append(["Employee ID", "Date", "Description", "Notes", "Request Status", "Approver", "Request Date"])
        ws.append(["LS417", "2026-09-05",
                   "requesting Attendance at 05 September 2026 on 05 September 2026 : for check in at no checkin and check out at 16:30",
                   "lupa absen pulang", "Approved", "LS007 Hikmat", "2026-09-06"])
    return _wb_bytes(build)


def upload_package(client, files: dict[str, bytes]):
    multipart = {kind: (f"{kind}.xlsx", data) for kind, data in files.items()}
    r = client.post("/uploads/package/preview", files=multipart)
    assert r.status_code == 200, r.text
    return r.json()


def confirm_package(client, upload_id, mode=None):
    body = {"mode": mode} if mode else None
    r = client.post(f"/uploads/package/{upload_id}/confirm", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_package_parses_all_sources_and_merges(client):
    body = upload_package(client, {
        "activity": activity_xlsx(), "report": report_xlsx(),
        "timeoff": timeoff_xlsx(), "approval": approval_xlsx(),
    })
    assert body["can_save"] is True, body
    kinds = {f["jenis_sumber"]: f for f in body["files"]}
    assert kinds["activity"]["row_count"] == 7
    assert kinds["report"]["row_count"] == 3                      # 2 baris subtotal dilewati
    assert any("subtotal" in w for w in kinds["report"]["warnings"])
    assert kinds["timeoff"]["row_count"] == 1
    assert kinds["approval"]["row_count"] == 1

    assert body["date_from"] == "2026-09-01" and body["date_to"] == "2026-09-05"
    assert body["employee_count"] == 3

    out = confirm_package(client, body["upload_id"])
    assert out["inserted"] == body["rekap_rows"] and out["employees"] == 3
    assert out["sumber"]["activity"] == 7 and out["sumber"]["report"] == 3

    by = {(r["nik"], r["tanggal"]): r for r in rows(client, page_size=100)}
    # LS019: clock in paling awal 08:20, clock out paling akhir 17:05
    assert by[("LS019", "2026-09-01")]["jam_masuk"] == "08:20" and by[("LS019", "2026-09-01")]["jam_keluar"] == "17:05"
    assert by[("LS019", "2026-09-01")]["status"] == "Hadir"


def test_clock_out_hilang_and_timeoff(client):
    body = upload_package(client, {"activity": activity_xlsx(), "report": report_xlsx(), "timeoff": timeoff_xlsx()})
    confirm_package(client, body["upload_id"])
    with SessionLocal() as db:
        ls021 = db.scalar(select(Attendance).where(Attendance.nik == "LS021", Attendance.tanggal == "2026-09-02"))
        assert ls021.clock_out_hilang is True and ls021.jam_keluar_menit is None
        assert ls021.status == "Hadir"  # hadir dengan tanda clock out hilang, bukan alpa

        # Time Off 09-04 -> status Sakit, bukan Alpa
        izin = db.scalar(select(Attendance).where(Attendance.nik == "LS021", Attendance.tanggal == "2026-09-04"))
        assert izin.status == "Sakit" and izin.izin_kode == "S"
        assert izin.menit_terlambat == 0


def test_approval_overrides_clock_and_flags_late_close(client):
    body = upload_package(client, {"report": report_xlsx(), "approval": approval_xlsx()})
    confirm_package(client, body["upload_id"])
    with SessionLocal() as db:
        cor = db.scalar(select(Attendance).where(Attendance.nik == "LS417", Attendance.tanggal == "2026-09-05"))
        assert cor.dikoreksi is True and cor.koreksi_approver == "LS007 Hikmat"
        assert cor.jam_keluar_menit == 16 * 60 + 30       # dari Description, menggantikan activity
        assert cor.jam_masuk_menit is None                 # "no checkin"
        # Koreksi 09-05 diajukan 09-06, masih bulan yang sama -> bukan "setelah tutup bulan".
        assert cor.koreksi_setelah_tutup is False


def test_approval_submitted_next_month_flags_late_close(client):
    def build(wb):
        ws = wb.active
        ws.title = "Approval"
        ws.append(["Employee ID", "Date", "Description", "Notes", "Request Status", "Approver", "Request Date"])
        ws.append(["LS417", "2026-09-30",
                   "for check in at 08:00 and check out at 16:30",
                   "lupa absen", "Approved", "LS007", "2026-10-02"])  # diajukan bulan berikutnya
    import io as _io
    wb2 = openpyxl.Workbook()
    build(wb2)
    buf = _io.BytesIO(); wb2.save(buf)

    body = upload_package(client, {"approval": buf.getvalue()})
    confirm_package(client, body["upload_id"])
    with SessionLocal() as db:
        cor = db.scalar(select(Attendance).where(Attendance.nik == "LS417", Attendance.tanggal == "2026-09-30"))
        assert cor.koreksi_setelah_tutup is True
        assert cor.jam_masuk_menit == 8 * 60 and cor.jam_keluar_menit == 16 * 60 + 30


def test_raw_tables_and_employee_master_saved(client):
    body = upload_package(client, {"activity": activity_xlsx(), "report": report_xlsx()})
    confirm_package(client, body["upload_id"])
    with SessionLocal() as db:
        assert db.scalar(select(AttendanceCorrection).where(AttendanceCorrection.id.is_not(None))) is None
        assert len(db.scalars(select(RawActivity)).all()) == 7
        assert len(db.scalars(select(RawReport)).all()) == 3
        assert db.get(Employee, "LS019").nama == "Kamaludin"
        assert db.get(Employee, "LS019").organisasi == "IT"


def test_reupload_replaces_same_range(client):
    files = {"activity": activity_xlsx(), "report": report_xlsx()}
    body = upload_package(client, files)
    first = confirm_package(client, body["upload_id"])
    body2 = upload_package(client, files)
    assert body2["conflict_count"] == first["inserted"]
    # tanpa mode -> semua baris lama ditimpa (default overwrite di save_rekap)
    second = confirm_package(client, body2["upload_id"], mode="overwrite")
    assert second["overwritten"] == first["inserted"]
    assert len(rows(client, page_size=100)) == first["inserted"]   # tidak dobel


def test_package_requires_hr(client):
    from tests.conftest import as_dept

    as_dept("Sales/Marketing")
    r = client.post("/uploads/package/preview", files={"activity": ("a.xlsx", activity_xlsx())})
    assert r.status_code == 403


def test_package_rejects_bad_columns(client):
    def build(wb):
        ws = wb.active
        ws.title = "Activity"
        ws.append(["Nama", "Lain"])           # tanpa Employee ID / tanggal
        ws.append(["x", "y"])
    data = _wb_bytes(build)
    r = client.post("/uploads/package/preview", files={"activity": ("a.xlsx", data)})
    body = r.json()
    assert body["can_save"] is False and any("activity" in e for e in body["errors"])
