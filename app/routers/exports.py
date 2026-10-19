import io
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import xlsxwriter
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import PERFORMANCE, require_department, scoped_filters
from app.models import Attendance as A, Department, User
from app.services import aggregator as agg
from app.services import audit
from app.services.rules import min_to_hhmm

router = APIRouter(prefix="/exports", tags=["exports"])

# Rekap Payroll: HR (ekspor) dan Audit (lihat). IT dan Sales/Marketing ditolak 403.
PAYROLL = Depends(require_department(Department.HR, Department.AUDIT))

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WIB = timezone(timedelta(hours=7))  # WIB tidak memakai DST, offset tetap aman di semua OS
MAX_DAILY_ROWS = 200_000  # batas baris sheet Data Harian
MAX_PDF_EMPLOYEES = 1000
MAX_PDF_LATE = 100


def _now() -> str:
    return f"{datetime.now(WIB):%Y-%m-%d %H:%M} WIB"


def _period_text(f: agg.Filters) -> str:
    return f"{f.date_from or 'awal data'} s/d {f.date_to or 'akhir data'}"


def _filter_lines(f: agg.Filters) -> list[tuple[str, str]]:
    return [
        ("Periode", _period_text(f)),
        ("Divisi", f.departemen or "Semua divisi"),
        ("Status", f.status or "Semua status"),
        ("Karyawan", f.karyawan or "Semua karyawan"),
    ]


def _disposition(ext: str, f: agg.Filters) -> dict:
    part = f"{f.date_from}_{f.date_to}" if f.date_from and f.date_to else "semua_periode"
    return {"Content-Disposition": f'attachment; filename="rekap_presensi_{part}.{ext}"'}


def _id(n: float, decimals: int = 0) -> str:
    """Format angka gaya Indonesia: 1.234.567,89"""
    s = f"{n:,.{decimals}f}"
    return s.replace(",", "#").replace(".", ",").replace("#", ".")


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{_id(v, 1)}%"


def _arrival_text(v: float | None) -> str:
    """Rata-rata kedatangan terhadap jadwal shift, dalam kata-kata."""
    if v is None:
        return "-"
    if abs(v) < 0.5:
        return "Tepat jadwal"
    n = _id(abs(v), 0)
    return f"{n} mnt lebih awal" if v < 0 else f"{n} mnt lebih lambat"


def _granularity(f: agg.Filters) -> str:
    return "day" if f.date_from and f.date_to and (f.date_to - f.date_from).days <= 62 else "month"


def _log(db: Session, request: Request, user: User, action: str, f: agg.Filters, rows: int) -> None:
    audit.log(
        db, user.id, action,
        {"from": str(f.date_from), "to": str(f.date_to), "departemen": f.departemen, "status": f.status, "karyawan": f.karyawan, "rows": rows},
        request.client.host if request.client else None,
    )
    db.commit()


@router.get("/excel")
def export_excel(
    request: Request,
    f: agg.Filters = Depends(scoped_filters),
    db: Session = Depends(get_db),
    user: User = PERFORMANCE,
):
    s = agg.summary(db, f)
    comp, trend = agg.status_composition(db, f), agg.trend(db, f, _granularity(f))
    depts, emps = agg.recap(db, f, "departemen"), agg.recap(db, f, "karyawan")
    truncated = s["records"] > MAX_DAILY_ROWS

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    title = wb.add_format({"bold": True, "font_size": 14})
    bold = wb.add_format({"bold": True})
    head = wb.add_format({"bold": True, "bg_color": "#E1EFEC", "border": 1, "text_wrap": True, "valign": "top"})
    int0 = wb.add_format({"num_format": "#,##0"})
    dec2 = wb.add_format({"num_format": "0.00"})
    pct = wb.add_format({"num_format": "0.0%"})
    datef = wb.add_format({"num_format": "yyyy-mm-dd"})
    note = wb.add_format({"italic": True, "font_color": "#B42318"})

    # ---- Ringkasan ----
    ws = wb.add_worksheet("Ringkasan")
    ws.write(0, 0, "Rekap Presensi Karyawan", title)
    for i, (k, v) in enumerate(_filter_lines(f), start=1):
        ws.write(i, 0, k, bold)
        ws.write(i, 1, v)
    ws.write(5, 0, "Dibuat", bold)
    ws.write(5, 1, f"{_now()} oleh {user.full_name}")
    ws.write(7, 0, "Ringkasan", head)
    ws.write(7, 1, "Nilai", head)
    kpis = [
        ("Tingkat kehadiran", None if s["kehadiran_pct"] is None else s["kehadiran_pct"] / 100, pct),
        ("Hari kerja efektif (baris)", s["hari_kerja"], int0),
        ("Hadir (termasuk dinas luar)", s["hadir"], int0),
        ("Terlambat (kali)", s["terlambat"], int0),
        ("Total menit terlambat", s["menit_terlambat"], int0),
        ("Izin", s["izin"], int0),
        ("Sakit", s["sakit"], int0),
        ("Cuti", s["cuti"], int0),
        ("Alpa", s["alpa"], int0),
        ("Dinas luar", s["dinas_luar"], int0),
        ("Rata-rata kedatangan terhadap jadwal", _arrival_text(s["avg_selisih_masuk_menit"]), None),
        ("Jumlah karyawan", s["karyawan"], int0),
        ("Jumlah baris data", s["records"], int0),
    ]
    for i, (label, val, fmt) in enumerate(kpis, start=8):
        ws.write(i, 0, label)
        if val is None:
            ws.write(i, 1, "-")
        elif isinstance(val, str):
            ws.write_string(i, 1, val)
        else:
            ws.write_number(i, 1, val, fmt)
    ws.write(7, 3, "Komposisi status", head)
    ws.write(7, 4, "Jumlah", head)
    for i, c in enumerate(comp, start=8):
        ws.write(i, 3, c["status"])
        ws.write_number(i, 4, c["total"], int0)
    t0 = 23
    ws.write(t0, 0, "Periode", head)
    ws.write(t0, 1, "Kehadiran", head)
    ws.write(t0, 2, "Terlambat", head)
    for i, p in enumerate(trend, start=t0 + 1):
        ws.write_string(i, 0, p["period"])
        if p["kehadiran_pct"] is None:
            ws.write(i, 1, "-")
        else:
            ws.write_number(i, 1, p["kehadiran_pct"] / 100, pct)
        ws.write_number(i, 2, p["terlambat"], int0)
    ws.set_column(0, 0, 30)
    ws.set_column(1, 1, 34)
    ws.set_column(2, 2, 12)
    ws.set_column(3, 3, 20)
    ws.set_column(4, 4, 12)
    if truncated:
        ws.write(6, 0, f"Catatan: sheet Data Harian dibatasi {_id(MAX_DAILY_ROWS)} baris pertama.", note)
    if any(c["total"] for c in comp):
        ch = wb.add_chart({"type": "column"})
        ch.add_series({"name": "Komposisi status", "categories": ["Ringkasan", 8, 3, 8 + len(comp) - 1, 3], "values": ["Ringkasan", 8, 4, 8 + len(comp) - 1, 4]})
        ch.set_title({"name": "Komposisi status"})
        ch.set_legend({"none": True})
        ws.insert_chart("G2", ch)
    if trend:
        ch2 = wb.add_chart({"type": "line"})
        ch2.add_series({"name": "Tingkat kehadiran", "categories": ["Ringkasan", t0 + 1, 0, t0 + len(trend), 0], "values": ["Ringkasan", t0 + 1, 1, t0 + len(trend), 1]})
        ch2.set_title({"name": "Tren tingkat kehadiran"})
        ch2.set_y_axis({"num_format": "0%", "min": 0, "max": 1})
        ch2.set_legend({"none": True})
        ws.insert_chart("G20", ch2)

    # ---- Rekap per Karyawan dan per Divisi ----
    def recap_sheet(name: str, rows: list[dict], first: list[tuple[str, str]], widths: list[int]):
        sh = wb.add_worksheet(name)
        heads = [h for h, _ in first] + [
            "Hari Kerja", "Hadir (termasuk Dinas Luar)", "Terlambat", "Total Menit Terlambat", "Pulang Cepat", "Total Menit Pulang Cepat",
            "Total Jam Kerja", "Total Menit Lembur", "Izin", "Sakit", "Cuti", "Alpa", "Dinas Luar", "Tingkat Kehadiran",
        ]
        for c, h in enumerate(heads):
            sh.write(0, c, h, head)
        for r, row in enumerate(rows, start=1):
            c = 0
            for _, key in first:
                v = row[key]
                sh.write_string(r, c, str(v)) if isinstance(v, str) else sh.write_number(r, c, v, int0)
                c += 1
            for key in ["hari_kerja", "hadir", "terlambat", "menit_terlambat", "pulang_cepat", "menit_pulang_cepat", "jam_kerja",
                        "menit_lembur", "izin", "sakit", "cuti", "alpa", "dinas_luar"]:
                sh.write_number(r, c, row[key], dec2 if key == "jam_kerja" else int0)
                c += 1
            if row["kehadiran_pct"] is None:
                sh.write(r, c, "-")
            else:
                sh.write_number(r, c, row["kehadiran_pct"] / 100, pct)
        for c, w in enumerate(widths):
            sh.set_column(c, c, w)
        sh.set_column(len(first), len(heads) - 1, 15)
        sh.freeze_panes(1, 0)
        sh.autofilter(0, 0, max(len(rows), 1), len(heads) - 1)

    recap_sheet("Rekap per Karyawan", emps, [("NIK", "nik"), ("Nama", "nama"), ("Divisi", "departemen")], [14, 28, 18])
    recap_sheet("Rekap per Divisi", depts, [("Divisi", "departemen"), ("Jumlah Karyawan", "karyawan")], [24, 12])

    # ---- Data Harian ----
    wd = wb.add_worksheet("Data Harian")
    cols = ["Tanggal", "NIK", "Nama", "Divisi", "Shift", "Jam Masuk", "Jam Keluar", "Status", "Menit Terlambat", "Menit Pulang Cepat", "Durasi Kerja (jam)", "Menit Lembur", "Lokasi Kerja", "Keterangan"]
    for c, h in enumerate(cols):
        wd.write(0, c, h, head)
    stmt = (
        select(A.tanggal, A.nik, A.nama, A.departemen, A.shift, A.jam_masuk_menit, A.jam_keluar_menit, A.status, A.menit_terlambat,
               A.menit_pulang_cepat, A.durasi_kerja, A.menit_lembur, A.lokasi_kerja, A.keterangan)
        .where(*agg._where(f))
        .order_by(A.tanggal, A.nik)
        .limit(MAX_DAILY_ROWS)
    )
    n = 0
    for n, r in enumerate(db.execute(stmt).yield_per(5000), start=1):
        wd.write_datetime(n, 0, datetime.combine(r.tanggal, datetime.min.time()), datef)
        wd.write_string(n, 1, r.nik)
        wd.write_string(n, 2, r.nama)
        wd.write_string(n, 3, r.departemen)
        wd.write_string(n, 4, r.shift)
        wd.write_string(n, 5, min_to_hhmm(r.jam_masuk_menit) or "")
        wd.write_string(n, 6, min_to_hhmm(r.jam_keluar_menit) or "")
        wd.write_string(n, 7, r.status)
        wd.write_number(n, 8, r.menit_terlambat, int0)
        wd.write_number(n, 9, r.menit_pulang_cepat, int0)
        if r.durasi_kerja is not None:
            wd.write_number(n, 10, r.durasi_kerja, dec2)
        wd.write_number(n, 11, r.menit_lembur, int0)
        wd.write_string(n, 12, r.lokasi_kerja or "")
        wd.write_string(n, 13, r.keterangan or "")
    for c, w in enumerate([12, 14, 26, 18, 10, 11, 11, 12, 11, 11, 12, 11, 14, 36]):
        wd.set_column(c, c, w)
    wd.freeze_panes(1, 0)
    wd.autofilter(0, 0, max(n, 1), len(cols) - 1)
    wb.close()

    _log(db, request, user, "export_excel", f, s["records"])
    return Response(buf.getvalue(), media_type=XLSX, headers=_disposition("xlsx", f))


def _table(data: list[list[str]], col_widths: list[float], right_from: int = 99) -> Table:
    t = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E1EFEC")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#C9D3D0")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    if right_from < len(col_widths):
        style.append(("ALIGN", (right_from, 0), (-1, -1), "RIGHT"))
    t.setStyle(TableStyle(style))
    return t


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "."


@router.get("/pdf")
def export_pdf(
    request: Request,
    f: agg.Filters = Depends(scoped_filters),
    db: Session = Depends(get_db),
    user: User = PERFORMANCE,
):
    s = agg.summary(db, f)
    comp = agg.status_composition(db, f)
    depts, emps = agg.recap(db, f, "departemen"), agg.recap(db, f, "karyawan")
    late_rows = db.execute(
        select(A.tanggal, A.nik, A.nama, A.departemen, A.jam_masuk_menit, A.menit_terlambat)
        .where(*agg._where(f), A.terlambat.is_(True))
        .order_by(A.menit_terlambat.desc(), A.tanggal, A.nik)
        .limit(MAX_PDF_LATE)
    ).all()

    st = getSampleStyleSheet()
    h2, body = st["Heading2"], st["BodyText"]
    info = "<br/>".join(f"{k}: {v}" for k, v in _filter_lines(f)) + f"<br/>Dibuat: {_now()} oleh {user.full_name}"
    story = [Paragraph("Rekap Presensi Karyawan", st["Title"]), Paragraph(info, body), Spacer(1, 8), Paragraph("Ringkasan periode", h2)]
    story.append(_table(
        [
            ["Tingkat kehadiran", "Hari kerja efektif", "Hadir", "Terlambat (kali)", "Total menit terlambat", "Tidak hadir", "Rata-rata kedatangan"],
            [_pct(s["kehadiran_pct"]), _id(s["hari_kerja"]), _id(s["hadir"]), _id(s["terlambat"]), _id(s["menit_terlambat"]), _id(s["tidak_hadir"]), _arrival_text(s["avg_selisih_masuk_menit"])],
        ],
        [3.6 * cm] * 7,
    ))
    story += [Spacer(1, 8), Paragraph("Komposisi status", h2),
              _table([["Status", "Jumlah"]] + [[c["status"], _id(c["total"])] for c in comp], [5 * cm, 3 * cm], 1)]

    if s["records"] == 0:
        story.append(Paragraph("Tidak ada data untuk filter ini.", body))
    else:
        story += [Spacer(1, 8), Paragraph("Rekap per divisi", h2), _table(
            [["Divisi", "Karyawan", "Hari kerja", "Hadir", "Terlambat", "Menit terlambat", "Izin", "Sakit", "Cuti", "Alpa", "Dinas luar", "Kehadiran"]]
            + [[_cut(d["departemen"], 28), _id(d["karyawan"]), _id(d["hari_kerja"]), _id(d["hadir"]), _id(d["terlambat"]), _id(d["menit_terlambat"]),
                _id(d["izin"]), _id(d["sakit"]), _id(d["cuti"]), _id(d["alpa"]), _id(d["dinas_luar"]), _pct(d["kehadiran_pct"])] for d in depts],
            [5 * cm] + [1.9 * cm] * 11, 1,
        )]
        shown = emps[:MAX_PDF_EMPLOYEES]
        story += [Spacer(1, 8), Paragraph("Rekap per karyawan", h2), _table(
            [["NIK", "Nama", "Divisi", "Hari kerja", "Hadir", "Terlambat", "Menit tlt", "Izin", "Sakit", "Cuti", "Alpa", "Dinas luar", "Kehadiran"]]
            + [[e["nik"], _cut(e["nama"], 26), _cut(e["departemen"], 18), _id(e["hari_kerja"]), _id(e["hadir"]), _id(e["terlambat"]), _id(e["menit_terlambat"]),
                _id(e["izin"]), _id(e["sakit"]), _id(e["cuti"]), _id(e["alpa"]), _id(e["dinas_luar"]), _pct(e["kehadiran_pct"])] for e in shown],
            [2.3 * cm, 5 * cm, 3.4 * cm] + [1.5 * cm] * 9 + [1.9 * cm], 3,
        )]
        if len(emps) > len(shown):
            story.append(Paragraph(f"Menampilkan {_id(len(shown))} dari {_id(len(emps))} karyawan. Gunakan Export Excel untuk data lengkap.", body))
        if late_rows:
            story += [Spacer(1, 8), Paragraph(f"Daftar keterlambatan (maksimal {MAX_PDF_LATE}, menit terlambat terbanyak)", h2), _table(
                [["Tanggal", "NIK", "Nama", "Divisi", "Jam masuk", "Menit terlambat"]]
                + [[str(r.tanggal), r.nik, _cut(r.nama, 30), _cut(r.departemen, 22), min_to_hhmm(r.jam_masuk_menit) or "-", _id(r.menit_terlambat)] for r in late_rows],
                [2.6 * cm, 2.8 * cm, 7 * cm, 5 * cm, 2.6 * cm, 3 * cm], 4,
            )]

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                      title="Rekap Presensi Karyawan").build(story)
    _log(db, request, user, "export_pdf", f, s["records"])
    return Response(buf.getvalue(), media_type="application/pdf", headers=_disposition("pdf", f))


MAX_PDF_EMPLOYEE_PAGES = 200  # batas jumlah halaman pada Rekap individual


def _employee_page(nik: str, detail: dict, f: agg.Filters, st) -> list:
    """Satu halaman untuk satu karyawan: identitas, ringkasan, dan tabel harian."""
    r = detail["rekap"]
    h2, body = st["Heading2"], st["BodyText"]
    page = [
        Paragraph(f"{detail['nama']} ({nik})", st["Title"]),
        Paragraph(f"Divisi: {detail['departemen'] or '-'} &nbsp;|&nbsp; Periode: {_period_text(f)}", body),
        Spacer(1, 6), Paragraph("Ringkasan", h2),
        _table(
            [["Kehadiran", "Hari kerja", "Hadir", "Terlambat", "Menit terlambat", "Tidak hadir", "Rata-rata kedatangan"],
             [_pct(r["kehadiran_pct"]), _id(r["hari_kerja"]), _id(r["hadir"]), _id(r["terlambat"]), _id(r["menit_terlambat"]),
              _id(r["tidak_hadir"]), _arrival_text(r["avg_selisih_masuk_menit"])]],
            [2.5 * cm] + [1.9 * cm] * 6,
        ),
        Spacer(1, 4),
        Paragraph(f"Izin {_id(r['izin'])}, sakit {_id(r['sakit'])}, cuti {_id(r['cuti'])}, alpa {_id(r['alpa'])}, dinas luar {_id(r['dinas_luar'])}.", body),
        Spacer(1, 8), Paragraph("Data harian", h2),
    ]
    rows = detail["range"]
    if rows:
        page.append(_table(
            [["Tanggal", "Masuk", "Keluar", "Terlambat (mnt)", "Durasi (jam)", "Status", "Keterangan"]]
            + [[str(x["tanggal"]), x["jam_masuk"] or "-", x["jam_keluar"] or "-", _id(x["menit_terlambat"]),
                "-" if x["durasi_kerja"] is None else _id(x["durasi_kerja"], 2), x["status"], _cut(x["keterangan"] or "-", 40)] for x in rows],
            [2.6 * cm, 2 * cm, 2 * cm, 2.9 * cm, 2.5 * cm, 2.6 * cm, 6.6 * cm], 3,
        ))
    else:
        page.append(Paragraph("Tidak ada data pada rentang ini.", body))
    return page


@router.get("/employee/pdf")
def export_employee_pdf(
    request: Request,
    nik: str | None = None,
    f: agg.Filters = Depends(scoped_filters),
    db: Session = Depends(get_db),
    user: User = PERFORMANCE,
):
    """Rekap individual: satu karyawan per halaman. Tanpa parameter nik, memuat semua karyawan pada filter (maksimal MAX_PDF_EMPLOYEE_PAGES)."""
    st = getSampleStyleSheet()
    story: list = []
    if nik:
        detail = agg.employee_detail(db, f, nik)
        if detail is None:
            raise HTTPException(404, "Karyawan tidak ditemukan pada rentang filter ini")
        story += _employee_page(nik, detail, f, st)
        count = 1
        filename = f"rekap_karyawan_{nik}"
    else:
        niks = [e["nik"] for e in agg.recap(db, f, "karyawan")]
        if not niks:
            raise HTTPException(404, "Tidak ada data pada rentang filter ini")
        shown = niks[:MAX_PDF_EMPLOYEE_PAGES]
        for i, n in enumerate(shown):
            detail = agg.employee_detail(db, f, n)
            if detail is None:
                continue
            if i:
                story.append(PageBreak())
            story += _employee_page(n, detail, f, st)
        if len(niks) > len(shown):
            story += [PageBreak(), Paragraph(f"Menampilkan {_id(len(shown))} dari {_id(len(niks))} karyawan. Gunakan Export Excel untuk data lengkap.", st["BodyText"])]
        count = len(shown)
        filename = f"rekap_karyawan_{f.date_from}_{f.date_to}" if f.date_from and f.date_to else "rekap_karyawan_semua_periode"

    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                      title="Rekap Individual Presensi").build(story)
    _log(db, request, user, "export_employee_pdf", replace(f, karyawan=nik) if nik else f, count)
    return Response(buf.getvalue(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{filename}.pdf"'})


@router.get("/employee/excel")
def export_employee_excel(
    request: Request,
    nik: str,
    f: agg.Filters = Depends(scoped_filters),
    db: Session = Depends(get_db),
    user: User = PERFORMANCE,
):
    """Rekap individu satu karyawan (Excel) untuk periode terpilih: sheet Rekap dan sheet Data Harian (PRD F21)."""
    detail = agg.employee_detail(db, f, nik)
    if detail is None:
        raise HTTPException(404, "Karyawan tidak ditemukan pada rentang filter ini")
    # Angka pulang cepat, jam kerja, dan lembur ada di rekap per karyawan, bukan di ringkasan detail.
    r = next((x for x in agg.recap(db, replace(f, karyawan=nik), "karyawan") if x["nik"] == nik), detail["rekap"])

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    title = wb.add_format({"bold": True, "font_size": 14})
    bold = wb.add_format({"bold": True})
    head = wb.add_format({"bold": True, "bg_color": "#E1EFEC", "border": 1, "text_wrap": True, "valign": "top"})
    text = wb.add_format({"num_format": "@"})
    int0 = wb.add_format({"num_format": "#,##0"})
    dec2 = wb.add_format({"num_format": "0.00"})
    pct = wb.add_format({"num_format": "0.0%"})
    datef = wb.add_format({"num_format": "yyyy-mm-dd"})
    note = wb.add_format({"italic": True, "font_color": "#B42318"})

    ws = wb.add_worksheet("Rekap")
    ws.write(0, 0, "Rekap Individual Presensi", title)
    for i, (k, v) in enumerate([("NIK", nik), ("Nama", detail["nama"]), ("Divisi", detail["departemen"] or "-"), ("Periode", _period_text(f))], start=1):
        ws.write(i, 0, k, bold)
        ws.write_string(i, 1, str(v), text)
    ws.write(5, 0, "Dibuat", bold)
    ws.write(5, 1, f"{_now()} oleh {user.full_name}")
    ws.write(7, 0, "Ringkasan", head)
    ws.write(7, 1, "Nilai", head)
    items = [
        ("Tingkat kehadiran", None if r["kehadiran_pct"] is None else r["kehadiran_pct"] / 100, pct),
        ("Hari kerja tercatat", r["hari_kerja"], int0),
        ("Hadir (termasuk dinas luar)", r["hadir"], int0),
        ("Terlambat (kali)", r["terlambat"], int0),
        ("Total menit terlambat", r["menit_terlambat"], int0),
        ("Pulang cepat (kali)", r["pulang_cepat"], int0),
        ("Total menit pulang cepat", r["menit_pulang_cepat"], int0),
        ("Izin", r["izin"], int0),
        ("Sakit", r["sakit"], int0),
        ("Cuti", r["cuti"], int0),
        ("Alpa", r["alpa"], int0),
        ("Dinas luar", r["dinas_luar"], int0),
        ("Total jam kerja", r["jam_kerja"], dec2),
        ("Jam kerja pada hari libur", r["jam_kerja_libur"], dec2),
        ("Total menit lembur", r["menit_lembur"], int0),
        ("Menit lembur pada hari libur", r["menit_lembur_libur"], int0),
        ("Rata-rata kedatangan terhadap jadwal", _arrival_text(detail["rekap"]["avg_selisih_masuk_menit"]), None),
    ]
    for i, (k, v, fmt) in enumerate(items, start=8):
        ws.write(i, 0, k)
        if v is None:
            ws.write_blank(i, 1, None)
        elif isinstance(v, str):
            ws.write_string(i, 1, v)
        else:
            ws.write_number(i, 1, v, fmt)
    ws.write(len(items) + 9, 0, "Catatan: koreksi disetujui dan jam kerja nyata belum tersedia sampai parser empat sumber (Rilis B).", note)
    ws.set_column(0, 0, 40)
    ws.set_column(1, 1, 34)

    wd = wb.add_worksheet("Data Harian")
    cols = ["Tanggal", "Jam masuk", "Jam keluar", "Menit terlambat", "Durasi kerja (jam)", "Status", "Keterangan"]
    for c, name in enumerate(cols):
        wd.write(0, c, name, head)
    for i, x in enumerate(detail["range"], start=1):
        wd.write_datetime(i, 0, x["tanggal"], datef)
        wd.write_string(i, 1, x["jam_masuk"] or "", text)
        wd.write_string(i, 2, x["jam_keluar"] or "", text)
        wd.write_number(i, 3, x["menit_terlambat"], int0)
        if x["durasi_kerja"] is None:
            wd.write_blank(i, 4, None)
        else:
            wd.write_number(i, 4, x["durasi_kerja"], dec2)
        wd.write_string(i, 5, x["status"])
        wd.write_string(i, 6, x["keterangan"] or "")
    wd.set_column(0, 0, 13)
    wd.set_column(1, 5, 16)
    wd.set_column(6, 6, 40)
    wd.freeze_panes(1, 0)
    wd.autofilter(0, 0, max(len(detail["range"]), 1), len(cols) - 1)
    wb.close()

    _log(db, request, user, "export_employee_excel", replace(f, karyawan=nik), 1)
    return Response(buf.getvalue(), media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="rekap_karyawan_{nik}.xlsx"'})


PAYROLL_COLUMNS = [
    ("nik", "NIK, disimpan sebagai teks agar angka nol di depan tidak hilang"),
    ("nama", "Nama karyawan"),
    ("departemen", "Divisi"),
    ("periode_awal", "Tanggal awal periode rekap"),
    ("periode_akhir", "Tanggal akhir periode rekap"),
    ("hari_kerja_periode", "Jumlah hari kerja menurut kalender pada periode (Senin sampai Sabtu, bukan hari libur terdaftar)"),
    ("hari_kerja_tercatat", "Jumlah hari kerja yang memiliki baris data untuk karyawan ini"),
    ("hari_tanpa_data", "hari_kerja_periode dikurangi hari_kerja_tercatat. Nilai di atas 0 berarti data belum lengkap, atau karyawan masuk atau keluar di tengah periode"),
    ("hari_hadir", "Hari berstatus Hadir pada hari kerja. Dinas Luar TIDAK termasuk, lihat hari_dinas_luar"),
    ("hari_dinas_luar", "Hari berstatus Dinas Luar pada hari kerja"),
    ("hari_izin", "Hari berstatus Izin pada hari kerja"),
    ("hari_sakit", "Hari berstatus Sakit pada hari kerja"),
    ("hari_cuti", "Hari berstatus Cuti pada hari kerja"),
    ("hari_alpa", "Hari Alpa pada hari kerja (tanpa jam masuk dan tanpa status izin, sakit, cuti, atau dinas luar)"),
    ("jumlah_terlambat", "Berapa kali terlambat pada hari kerja"),
    ("total_menit_terlambat", "Jumlah menit terlambat = jam masuk dikurangi jam mulai shift dan toleransi, per hari, dijumlahkan"),
    ("jumlah_pulang_cepat", "Berapa kali pulang sebelum jam selesai shift pada hari kerja"),
    ("total_menit_pulang_cepat", "Jumlah menit pulang cepat"),
    ("total_jam_kerja", "Jumlah durasi kerja (jam keluar dikurangi jam masuk) pada seluruh hari, dalam jam"),
    ("jam_kerja_hari_libur", "Bagian dari total_jam_kerja yang terjadi pada hari Minggu atau hari libur"),
    ("total_menit_lembur", "Menit kerja setelah jam selesai shift, semua hari. Kerja di hari libur hanya terhitung lembur untuk menit setelah jam selesai shift"),
    ("menit_lembur_hari_libur", "Bagian dari total_menit_lembur yang terjadi pada hari Minggu atau hari libur"),
    ("tingkat_kehadiran_persen", "(hari_hadir ditambah hari_dinas_luar) dibagi hari_kerja_tercatat, dikali 100"),
    ("status_periode", "DRAFT: periode belum ditutup. Fitur Tutup periode belum tersedia sehingga semua rekap berstatus DRAFT"),
]


@router.get("/payroll")
def export_payroll(
    request: Request,
    f: agg.Filters = Depends(scoped_filters),
    db: Session = Depends(get_db),
    user: User = PAYROLL,
):
    """Rekap Payroll: satu baris per karyawan, angka polos tanpa pemisah ribuan agar mudah dipakai rumus atau VLOOKUP."""
    if not (f.date_from and f.date_to):
        raise HTTPException(422, "Pilih rentang tanggal (dari dan sampai) untuk Rekap Payroll")
    if (f.date_to - f.date_from).days > 366:
        raise HTTPException(422, "Rentang Rekap Payroll maksimal satu tahun")
    rows = agg.payroll_rows(db, f)

    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    head = wb.add_format({"bold": True, "bg_color": "#E1EFEC", "border": 1, "text_wrap": True, "valign": "top"})
    text = wb.add_format({"num_format": "@"})
    int0 = wb.add_format({"num_format": "0"})
    dec2 = wb.add_format({"num_format": "0.00"})
    dec1 = wb.add_format({"num_format": "0.0"})
    bold = wb.add_format({"bold": True})

    ws = wb.add_worksheet("Rekap Payroll")
    for c, (name, _) in enumerate(PAYROLL_COLUMNS):
        ws.write(0, c, name, head)
    start, end = str(f.date_from), str(f.date_to)
    for r, row in enumerate(rows, start=1):
        values = [
            row["nik"], row["nama"], row["departemen"], start, end,
            row["hari_kerja_periode"], row["hari_kerja"], row["hari_tanpa_data"], row["hari_hadir"], row["dinas_luar"],
            row["izin"], row["sakit"], row["cuti"], row["alpa"],
            row["terlambat"], row["menit_terlambat"], row["pulang_cepat"], row["menit_pulang_cepat"],
            row["jam_kerja"], row["jam_kerja_libur"], row["menit_lembur"], row["menit_lembur_libur"],
            row["kehadiran_pct"], "DRAFT",
        ]
        for c, v in enumerate(values):
            name = PAYROLL_COLUMNS[c][0]
            if isinstance(v, str):
                ws.write_string(r, c, v, text if name in ("nik", "nama", "departemen", "periode_awal", "periode_akhir") else None)
            elif v is None:
                ws.write_blank(r, c, None)  # tingkat kehadiran kosong bila tidak ada hari kerja tercatat
            elif name in ("total_jam_kerja", "jam_kerja_hari_libur"):
                ws.write_number(r, c, v, dec2)
            elif name == "tingkat_kehadiran_persen":
                ws.write_number(r, c, v, dec1)
            else:
                ws.write_number(r, c, v, int0)
    ws.set_column(0, 0, 14)
    ws.set_column(1, 1, 26)
    ws.set_column(2, 2, 18)
    ws.set_column(3, len(PAYROLL_COLUMNS) - 1, 14)
    ws.freeze_panes(1, 3)
    ws.autofilter(0, 0, max(len(rows), 1), len(PAYROLL_COLUMNS) - 1)

    notes = wb.add_worksheet("Catatan")
    notes.set_column(0, 0, 30)
    notes.set_column(1, 1, 120)
    notes.write(0, 0, "Rekap Payroll (DRAFT)", bold)
    info = [("Periode", _period_text(f)), ("Divisi", f.departemen or "Semua divisi"), ("Karyawan", f.karyawan or "Semua karyawan"),
            ("Dibuat", f"{_now()} oleh {user.full_name}"), ("Jumlah karyawan", str(len(rows)))]
    for i, (k, v) in enumerate(info, start=1):
        notes.write(i, 0, k, bold)
        notes.write(i, 1, v)
    notes.write(len(info) + 2, 0, "Kolom", head)
    notes.write(len(info) + 2, 1, "Arti", head)
    for i, (name, desc) in enumerate(PAYROLL_COLUMNS, start=len(info) + 3):
        notes.write(i, 0, name)
        notes.write(i, 1, desc)
    tail = len(info) + len(PAYROLL_COLUMNS) + 4
    notes.write(tail, 0, "Perlu diperhatikan", bold)
    for i, line in enumerate([
        "Sistem tidak menghitung gaji, tunjangan, potongan, atau nominal lembur. Angka di sheet ini adalah masukan untuk perhitungan tersebut.",
        "Pastikan hari_tanpa_data bernilai 0 untuk karyawan yang bekerja penuh sebelum rekap dipakai untuk payslip.",
        "Filter status pada dashboard diabaikan di sheet ini agar seluruh status ikut terhitung.",
    ], start=tail + 1):
        notes.write(i, 1, line)
    wb.close()

    _log(db, request, user, "export_payroll", f, len(rows))
    return Response(
        buf.getvalue(),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="rekap_payroll_{f.date_from}_{f.date_to}_DRAFT.xlsx"'},
    )
