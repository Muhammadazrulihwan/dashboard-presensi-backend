"""Kelengkapan Data (PRD F12).

Menghitung indikator yang bisa dibangun dari model `attendance` saat ini (satu file template):

- Hari kerja kalender vs hari kerja tercatat per karyawan (hari tanpa data).
- Baris dengan jam masuk ada tetapi jam keluar kosong (clock out hilang).
- Baris dengan jam keluar ada tetapi jam masuk kosong (clock in hilang).
- Cakupan data per bulan (periode_ym) pada rentang filter.
- Status sumber data: sumber tunggal template tersedia; empat sumber PRD (Activity, Report
  Attendance, Time Off, Approval) belum tersedia sampai parser Rilis B dibangun.

Indikator PRD lain yang menunggu Rilis B (selisih terhadap Report Attendance dan koreksi setelah
tutup bulan) sengaja tidak dihitung di sini karena datanya belum ada.
"""
from dataclasses import replace

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import Attendance as A
from app.services.aggregator import Filters, _where, recap, workdays_between
from app.services.rules import min_to_hhmm

# Empat sumber PRD bagian 3 ditambah sumber template yang dipakai sekarang.
SUMBER = [
    {"kode": "presensi", "nama": "Presensi (file template)", "status": "tersedia",
     "catatan": "Sumber tunggal yang dipakai upload saat ini."},
    {"kode": "activity", "nama": "Activity", "status": "belum",
     "catatan": "Parser empat sumber belum dibangun (Rilis B)."},
    {"kode": "report", "nama": "Report Attendance", "status": "belum",
     "catatan": "Parser empat sumber belum dibangun (Rilis B)."},
    {"kode": "timeoff", "nama": "Time Off", "status": "belum",
     "catatan": "Parser empat sumber belum dibangun (Rilis B)."},
    {"kode": "approval", "nama": "Approval", "status": "belum",
     "catatan": "Parser empat sumber belum dibangun (Rilis B)."},
]


def _range(db: Session, f: Filters) -> tuple:
    """Rentang tanggal efektif. Bila filter kosong, pakai rentang data yang ada."""
    if f.date_from and f.date_to:
        return f.date_from, f.date_to
    lo, hi = db.execute(select(func.min(A.tanggal), func.max(A.tanggal)).where(*_where(replace(f, date_from=None, date_to=None)))).one()
    return (f.date_from or lo), (f.date_to or hi)


def _missing(db: Session, f: Filters, kind: str) -> list[dict]:
    """Baris yang hanya punya satu sisi ketukan.

    kind="out": jam masuk ada, jam keluar kosong. kind="in": sebaliknya.
    """
    has, empty = (A.jam_masuk_menit, A.jam_keluar_menit) if kind == "out" else (A.jam_keluar_menit, A.jam_masuk_menit)
    rows = db.execute(
        select(A.nik, A.nama, A.departemen, A.tanggal, A.jam_masuk_menit, A.jam_keluar_menit)
        .where(*_where(replace(f, status=None)), has.is_not(None), empty.is_(None))
        .order_by(A.tanggal, A.nik)
    ).all()
    return [
        {"nik": r.nik, "nama": r.nama, "departemen": r.departemen, "tanggal": r.tanggal,
         "jam_masuk": min_to_hhmm(r.jam_masuk_menit), "jam_keluar": min_to_hhmm(r.jam_keluar_menit)}
        for r in rows
    ]


def completeness(db: Session, f: Filters) -> dict:
    """Ringkasan kelengkapan data pada rentang filter."""
    d1, d2 = _range(db, f)
    if d1 is None or d2 is None:
        # Tidak ada data sama sekali pada batas filter.
        return {
            "periode": {"from": None, "to": None},
            "hari_kerja_kalender": 0,
            "ringkasan": {
                "karyawan": 0, "karyawan_lengkap": 0, "karyawan_tanpa_data": 0,
                "hari_tanpa_data": 0, "baris_clock_out_hilang": 0, "baris_clock_in_hilang": 0,
            },
            "karyawan_tanpa_data": [],
            "clock_out_hilang": [],
            "clock_in_hilang": [],
            "cakupan_bulan": [],
            "sumber": SUMBER,
        }

    f = replace(f, date_from=d1, date_to=d2, status=None)
    expected = workdays_between(db, d1, d2)

    per_emp = recap(db, f, "karyawan")
    tanpa_data = []
    for r in per_emp:
        selisih = max(0, expected - r["hari_kerja"])
        if selisih > 0:
            tanpa_data.append({
                "nik": r["nik"], "nama": r["nama"], "departemen": r["departemen"],
                "hari_kerja_tercatat": r["hari_kerja"], "hari_tanpa_data": selisih,
            })
    tanpa_data.sort(key=lambda x: (-x["hari_tanpa_data"], x["departemen"] or "", x["nik"]))

    out_rows = _missing(db, f, "out")
    in_rows = _missing(db, f, "in")

    coverage = db.execute(
        select(
            A.periode_ym.label("periode"),
            func.count(func.distinct(A.nik)).label("karyawan"),
            func.count(A.id).label("baris"),
            func.coalesce(func.sum(case((A.hari_kerja.is_(True), 1), else_=0)), 0).label("hari_kerja"),
        )
        .where(*_where(f))
        .group_by(A.periode_ym)
        .order_by(A.periode_ym)
    ).all()

    return {
        "periode": {"from": d1, "to": d2},
        "hari_kerja_kalender": expected,
        "ringkasan": {
            "karyawan": len(per_emp),
            "karyawan_lengkap": len(per_emp) - len(tanpa_data),
            "karyawan_tanpa_data": len(tanpa_data),
            "hari_tanpa_data": sum(x["hari_tanpa_data"] for x in tanpa_data),
            "baris_clock_out_hilang": len(out_rows),
            "baris_clock_in_hilang": len(in_rows),
        },
        "karyawan_tanpa_data": tanpa_data,
        "clock_out_hilang": out_rows,
        "clock_in_hilang": in_rows,
        "cakupan_bulan": [
            {"periode": r.periode, "karyawan": int(r.karyawan), "baris": int(r.baris), "hari_kerja": int(r.hari_kerja)}
            for r in coverage
        ],
        "sumber": SUMBER,
    }
