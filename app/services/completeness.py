"""Kelengkapan Data (PRD F11, F12).

Indikator yang dihitung dari Rekap Absensi terpadu:

- Hari kerja kalender vs hari kerja tercatat per karyawan (hari tanpa data).
- Baris dengan jam masuk ada tetapi jam keluar kosong (clock out hilang) dan sebaliknya.
- Cakupan data per bulan (periode_ym) pada rentang filter.
- Selisih terhadap Report Attendance (F11): baris yang hasil sistemnya berbeda dari report.
- Koreksi setelah tutup bulan (F12): koreksi yang diajukan setelah bulan efektifnya lewat.
- Status sumber data: empat sumber PRD (Activity, Report Attendance, Time Off, Approval) tersedia
  lewat upload paket; sumber template lama juga masih diterima.
"""
from dataclasses import replace

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    Attendance as A,
    AttendanceCorrection,
    RawActivity,
    RawReport,
    TimeOffRequest,
)
from app.services.aggregator import Filters, _where, recap, workdays_between
from app.services.rules import min_to_hhmm

# Empat sumber PRD bagian 3 ditambah sumber template yang masih diterima.
# Status "tersedia"/"belum" dihitung dari data yang benar-benar masuk pada rentang filter
# (lihat _sumber_status), bukan lagi daftar statis.
SUMBER = [
    {"kode": "activity", "nama": "Activity",
     "catatan": "Jam masuk dan pulang mentah; clock in paling awal dan clock out paling akhir."},
    {"kode": "report", "nama": "Report Attendance",
     "catatan": "Jadwal shift dan pembanding; selisih ditandai, tidak menimpa hasil sistem."},
    {"kode": "timeoff", "nama": "Time Off",
     "catatan": "Izin, cuti, sakit yang disetujui menentukan status hari."},
    {"kode": "approval", "nama": "Approval",
     "catatan": "Koreksi absen disetujui menimpa jam clock in/out pada tanggal efektif."},
    {"kode": "presensi", "nama": "Presensi (file template)",
     "catatan": "Sumber tunggal lama; tetap didukung untuk upload satu file."},
]


def _count_rows(db: Session, model, conds) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*conds)) or 0


def _sumber_status(db: Session, d1, d2) -> list[dict]:
    """Tandai tiap sumber 'tersedia' bila ada data pada rentang, 'belum' bila tidak.

    Rentang tanggal per sumber memakai kolom tanggal efektifnya masing-masing. Time Off
    memakai tumpang tindih (mulai..selesai) karena satu pengajuan bisa melintasi batas rentang.
    """
    ada = {
        "activity": _count_rows(db, RawActivity, [RawActivity.tanggal >= d1, RawActivity.tanggal <= d2]),
        "report": _count_rows(db, RawReport, [RawReport.tanggal >= d1, RawReport.tanggal <= d2]),
        "timeoff": _count_rows(db, TimeOffRequest, [
            TimeOffRequest.tanggal_mulai <= d2,
            or_(TimeOffRequest.tanggal_selesai >= d1, TimeOffRequest.tanggal_selesai.is_(None)),
        ]),
        "approval": _count_rows(db, AttendanceCorrection, [
            AttendanceCorrection.tanggal_efektif >= d1, AttendanceCorrection.tanggal_efektif <= d2,
        ]),
        "presensi": _count_rows(db, A, [A.tanggal >= d1, A.tanggal <= d2]),
    }
    out = []
    for s in SUMBER:
        n = ada[s["kode"]]
        out.append({
            **s,
            "status": "tersedia" if n > 0 else "belum",
            "catatan": s["catatan"] if n > 0 else f"Belum ada data {s['nama']} untuk rentang ini. Unggah lewat Upload Paket 4 Sumber.",
        })
    return out



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


def _selisih(db: Session, f: Filters) -> list[dict]:
    """Baris yang hasil sistemnya berbeda dari Report Attendance (PRD F11)."""
    rows = db.execute(
        select(A.nik, A.nama, A.departemen, A.tanggal, A.selisih_report, A.menit_terlambat)
        .where(*_where(replace(f, status=None)), A.selisih_report.is_not(None))
        .order_by(A.tanggal, A.nik)
    ).all()
    return [
        {"nik": r.nik, "nama": r.nama, "departemen": r.departemen, "tanggal": r.tanggal,
         "selisih": r.selisih_report, "menit_terlambat": r.menit_terlambat}
        for r in rows
    ]


def _koreksi_tutup(db: Session, f: Filters) -> list[dict]:
    """Koreksi yang diajukan setelah bulan efektifnya lewat (PRD F12)."""
    rows = db.execute(
        select(A.nik, A.nama, A.departemen, A.tanggal, A.koreksi_alasan, A.koreksi_approver)
        .where(*_where(replace(f, status=None)), A.koreksi_setelah_tutup.is_(True))
        .order_by(A.tanggal, A.nik)
    ).all()
    return [
        {"nik": r.nik, "nama": r.nama, "departemen": r.departemen, "tanggal": r.tanggal,
         "alasan": r.koreksi_alasan, "approver": r.koreksi_approver}
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
                "baris_selisih_report": 0, "baris_koreksi_setelah_tutup": 0,
            },
            "karyawan_tanpa_data": [],
            "clock_out_hilang": [],
            "clock_in_hilang": [],
            "selisih_report": [],
            "koreksi_setelah_tutup": [],
            "cakupan_bulan": [],
            "sumber": [
                {**s, "status": "belum",
                 "catatan": f"Belum ada data {s['nama']} untuk rentang ini."}
                for s in SUMBER
            ],
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
    selisih_rows = _selisih(db, f)
    koreksi_rows = _koreksi_tutup(db, f)

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
            "baris_selisih_report": len(selisih_rows),
            "baris_koreksi_setelah_tutup": len(koreksi_rows),
        },
        "karyawan_tanpa_data": tanpa_data,
        "clock_out_hilang": out_rows,
        "clock_in_hilang": in_rows,
        "selisih_report": selisih_rows,
        "koreksi_setelah_tutup": koreksi_rows,
        "cakupan_bulan": [
            {"periode": r.periode, "karyawan": int(r.karyawan), "baris": int(r.baris), "hari_kerja": int(r.hari_kerja)}
            for r in coverage
        ],
        "sumber": _sumber_status(db, d1, d2),
    }
