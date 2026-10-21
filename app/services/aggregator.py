from dataclasses import dataclass, replace
from datetime import date, timedelta

from fastapi import HTTPException, Query
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import PRESENT, STATUSES, Attendance as A, Holiday, ShiftRule
from app.services.rules import min_to_hhmm

COMPOSITION = ["Hadir", "Terlambat", "Izin", "Sakit", "Cuti", "Alpa", "Dinas Luar"]
ALLOWED_STATUS = set(STATUSES) | {"Terlambat", "Libur"}


@dataclass
class Filters:
    date_from: date | None = None
    date_to: date | None = None
    departemen: str | None = None
    status: str | None = None
    karyawan: str | None = None
    # Batas data menurut departemen akun (nama departemen pada data karyawan, huruf kecil). None = semua karyawan.
    scope: tuple[str, ...] | None = None


def _parse_date(v: str | None, label: str) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(v)
    except ValueError:
        raise HTTPException(422, f"Format {label} harus YYYY-MM-DD")


def get_filters(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    departemen: str | None = Query(None),
    status: str | None = Query(None),
    karyawan: str | None = Query(None),
) -> Filters:
    """Dipakai bersama oleh dashboard dan export, supaya hasil export selalu sama dengan layar."""
    d1, d2 = _parse_date(from_, "tanggal awal"), _parse_date(to, "tanggal akhir")
    if d1 and d2 and d1 > d2:
        raise HTTPException(422, "Tanggal awal tidak boleh setelah tanggal akhir")
    if status and status not in ALLOWED_STATUS:
        raise HTTPException(422, "Status tidak valid")
    return Filters(d1, d2, departemen or None, status or None, (karyawan or "").strip() or None)


def previous_filters(f: Filters) -> Filters | None:
    """Periode pembanding: rentang yang sama panjangnya tepat sebelum periode aktif."""
    if not (f.date_from and f.date_to):
        return None
    length = (f.date_to - f.date_from).days + 1
    prev_to = f.date_from - timedelta(days=1)
    return replace(f, date_from=prev_to - timedelta(days=length - 1), date_to=prev_to)


def _like(col, text: str):
    esc = text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return func.lower(col).like(f"%{esc}%", escape="\\")


def _where(f: Filters) -> list:
    c = []
    if f.scope is not None:
        c.append(func.lower(A.departemen).in_(f.scope))
    if f.date_from:
        c.append(A.tanggal >= f.date_from)
    if f.date_to:
        c.append(A.tanggal <= f.date_to)
    if f.departemen:
        c.append(A.departemen == f.departemen)
    if f.status == "Terlambat":
        c += [A.status == "Hadir", A.terlambat.is_(True)]
    elif f.status:
        c.append(A.status == f.status)
    if f.karyawan:
        c.append(or_(A.nik == f.karyawan, _like(A.nama, f.karyawan)))
    return c


def _cnt(cond):
    return func.coalesce(func.sum(case((cond, 1), else_=0)), 0)


def _counters() -> dict:
    hk = A.hari_kerja.is_(True)
    return {
        "hari_kerja": _cnt(hk),
        "hadir": _cnt(and_(hk, A.status.in_(PRESENT))),
        # Keterlambatan hanya bermakna pada hari kerja (hari libur tidak punya jam mulai shift).
        "terlambat": _cnt(and_(hk, A.terlambat.is_(True))),
        "menit_terlambat": func.coalesce(func.sum(case((hk, A.menit_terlambat), else_=0)), 0),
        "izin": _cnt(and_(hk, A.status == "Izin")),
        "sakit": _cnt(and_(hk, A.status == "Sakit")),
        "cuti": _cnt(and_(hk, A.status == "Cuti")),
        "alpa": _cnt(and_(hk, A.status == "Alpa")),
        "dinas_luar": _cnt(and_(hk, A.status == "Dinas Luar")),
        # Rekap Absensi terpadu (Rilis B): koreksi dan pembanding Report Attendance.
        "dikoreksi": _cnt(and_(hk, A.dikoreksi.is_(True))),
        "selisih_report": _cnt(and_(hk, A.selisih_report.is_not(None))),
        "clock_out_hilang": _cnt(and_(hk, A.clock_out_hilang.is_(True))),
        "koreksi_setelah_tutup": _cnt(A.koreksi_setelah_tutup.is_(True)),
    }


def _extra_counters() -> dict:
    """Angka tambahan untuk rekap per karyawan dan per divisi: pulang cepat, jam kerja, dan lembur."""
    hk = A.hari_kerja.is_(True)
    off = A.hari_kerja.is_(False)
    return {
        "pulang_cepat": _cnt(and_(hk, A.menit_pulang_cepat > 0)),
        "menit_pulang_cepat": func.coalesce(func.sum(case((hk, A.menit_pulang_cepat), else_=0)), 0),
        "jam_kerja": func.coalesce(func.sum(A.durasi_kerja), 0),
        "jam_kerja_libur": func.coalesce(func.sum(case((off, A.durasi_kerja), else_=0)), 0),
        "menit_lembur": func.coalesce(func.sum(A.menit_lembur), 0),  # semua hari, termasuk hari libur
        "menit_lembur_libur": func.coalesce(func.sum(case((off, A.menit_lembur), else_=0)), 0),
        # Jam kerja nyata (Real Working Hour) dari parser empat sumber; kosong pada data template lama.
        "jam_kerja_nyata": func.coalesce(func.sum(A.jam_kerja_nyata), 0),
    }


_FLOATS = {"jam_kerja", "jam_kerja_libur", "jam_kerja_nyata"}


def _finish(row: dict) -> dict:
    d = {k: (round(float(v), 2) if k in _FLOATS else int(v)) for k, v in row.items() if v is not None}
    d["tidak_hadir"] = d["izin"] + d["sakit"] + d["cuti"] + d["alpa"]
    d["kehadiran_pct"] = round(d["hadir"] * 100 / d["hari_kerja"], 1) if d["hari_kerja"] else None
    return d


def summary(db: Session, f: Filters) -> dict:
    hadir_masuk = and_(A.status == "Hadir", A.jam_masuk_menit.is_not(None))
    avg_masuk = func.avg(case((hadir_masuk, A.jam_masuk_menit)))
    # Selisih kedatangan terhadap jam mulai shift masing-masing (positif = lebih lambat dari jadwal).
    # Dinormalisasi ke rentang -720 sampai 719 menit agar kedatangan setelah tengah malam pada shift malam tidak melenceng.
    # Berbeda dengan rata-rata jam, angka ini tetap bermakna bila ada lebih dari satu shift.
    diff = ((A.jam_masuk_menit - ShiftRule.jam_masuk_menit + 720) % 1440 + 1440) % 1440 - 720
    avg_diff = func.avg(case((and_(hadir_masuk, ShiftRule.jam_masuk_menit.is_not(None)), diff)))
    cols = {**_counters(), "karyawan": func.count(func.distinct(A.nik)), "records": func.count(A.id),
            "jam_kerja_nyata": func.coalesce(func.sum(A.jam_kerja_nyata), 0)}
    stmt = (
        select(*[v.label(k) for k, v in cols.items()], avg_masuk.label("avg_masuk"), avg_diff.label("avg_selisih"))
        .select_from(A)
        .outerjoin(ShiftRule, ShiftRule.kode == A.shift)
        .where(*_where(f))
    )
    row = dict(db.execute(stmt).mappings().one())
    avg = row.pop("avg_masuk")
    avg_selisih = row.pop("avg_selisih")
    out = _finish(row)
    out["avg_jam_masuk_menit"] = round(float(avg), 1) if avg is not None else None
    out["avg_jam_masuk"] = min_to_hhmm(avg)
    out["avg_selisih_masuk_menit"] = round(float(avg_selisih), 1) if avg_selisih is not None else None
    return out


def trend(db: Session, f: Filters, granularity: str) -> list[dict]:
    key = A.tanggal if granularity == "day" else A.periode_ym
    c = _counters()
    rows = db.execute(
        select(key.label("k"), c["hari_kerja"].label("hk"), c["hadir"].label("hadir"), c["terlambat"].label("late"))
        .where(*_where(f))
        .group_by(key)
        .order_by(key)
    ).all()
    out = []
    for k, hk, hadir, late in rows:
        out.append({
            "period": k.isoformat() if isinstance(k, date) else str(k),
            "kehadiran_pct": round(int(hadir) * 100 / int(hk), 1) if int(hk) else None,
            "terlambat": int(late),
        })
    return out


def status_composition(db: Session, f: Filters) -> list[dict]:
    rows = db.execute(
        select(A.status, A.terlambat, func.count(A.id)).where(*_where(f), A.hari_kerja.is_(True)).group_by(A.status, A.terlambat)
    ).all()
    counts = {k: 0 for k in COMPOSITION}
    for status, late, n in rows:
        key = ("Terlambat" if late else "Hadir") if status == "Hadir" else status
        if key in counts:
            counts[key] += int(n)
    return [{"status": k, "total": counts[k]} for k in COMPOSITION]


def recap(db: Session, f: Filters, group: str) -> list[dict]:
    """Rekap per divisi ('departemen') atau per karyawan ('karyawan')."""
    c = [v.label(k) for k, v in {**_counters(), **_extra_counters()}.items()]
    if group == "departemen":
        stmt = select(A.departemen.label("departemen"), func.count(func.distinct(A.nik)).label("karyawan"), *c).group_by(A.departemen).order_by(A.departemen)
    else:
        stmt = (
            select(A.nik.label("nik"), func.max(A.nama).label("nama"), func.max(A.departemen).label("departemen"), *c)
            .group_by(A.nik)
            .order_by(func.max(A.departemen), A.nik)
        )
    out = []
    for r in db.execute(stmt.where(*_where(f))).mappings():
        r = dict(r)
        ident = {k: r.pop(k) for k in ("departemen", "nik", "nama", "karyawan") if k in r}
        out.append({**ident, **_finish(r)})
    return out


def top_employees(db: Session, f: Filters, metric: str, limit: int) -> list[dict]:
    c = _counters()
    late, mnt, alpa = c["terlambat"].label("terlambat"), c["menit_terlambat"].label("menit_terlambat"), c["alpa"].label("alpa")
    stmt = select(A.nik, func.max(A.nama).label("nama"), func.max(A.departemen).label("departemen"), late, mnt, alpa).where(*_where(f)).group_by(A.nik)
    if metric == "alpa":
        stmt = stmt.having(c["alpa"] > 0).order_by(c["alpa"].desc(), A.nik)
    else:
        stmt = stmt.having(c["terlambat"] > 0).order_by(c["terlambat"].desc(), c["menit_terlambat"].desc(), A.nik)
    return [{**dict(r), "terlambat": int(r.terlambat), "menit_terlambat": int(r.menit_terlambat), "alpa": int(r.alpa)}
            for r in db.execute(stmt.limit(limit)).mappings()]


SORTABLE = {
    "tanggal": A.tanggal, "nik": A.nik, "nama": A.nama, "departemen": A.departemen, "jam_masuk": A.jam_masuk_menit,
    "menit_terlambat": A.menit_terlambat, "durasi_kerja": A.durasi_kerja, "status": A.status,
}


def records_page(db: Session, f: Filters, page: int, page_size: int, sort: str, order: str, q: str | None) -> tuple[list[A], int]:
    conds = _where(f)
    if q:
        conds.append(or_(_like(A.nik, q), _like(A.nama, q)))
    total = db.scalar(select(func.count(A.id)).where(*conds)) or 0
    col = SORTABLE.get(sort, A.tanggal)
    col = col.desc() if order == "desc" else col.asc()
    items = db.scalars(select(A).where(*conds).order_by(col, A.tanggal, A.nik).offset((page - 1) * page_size).limit(page_size)).all()
    return list(items), total


def meta(db: Session, scope: tuple[str, ...] | None) -> dict:
    where = [func.lower(A.departemen).in_(scope)] if scope is not None else []
    lo, hi = db.execute(select(func.min(A.tanggal), func.max(A.tanggal)).where(*where)).one()
    depts = list(db.scalars(select(A.departemen).where(*where).distinct().order_by(A.departemen)))
    return {"min_date": lo, "max_date": hi, "departemen": depts, "statuses": ["Hadir", "Terlambat", "Izin", "Sakit", "Cuti", "Dinas Luar", "Alpa"]}


def employee_detail(db: Session, f: Filters, nik: str) -> dict | None:
    """Profil dan rekap satu karyawan pada rentang filter. None bila NIK tidak ada."""
    ef = replace(f, karyawan=nik)
    rows = db.scalars(select(A).where(*_where(ef)).order_by(A.tanggal)).all()
    if not rows:
        return None
    rekap = summary(db, ef)
    range_items = [
        {
            "id": r.id, "tanggal": r.tanggal, "nik": r.nik, "nama": r.nama, "departemen": r.departemen,
            "jam_masuk": min_to_hhmm(r.jam_masuk_menit), "jam_keluar": min_to_hhmm(r.jam_keluar_menit),
            "menit_terlambat": r.menit_terlambat, "durasi_kerja": r.durasi_kerja, "status": r.status, "keterangan": r.keterangan,
            # Rekap terpadu (Rilis B): jejak koreksi dan pembanding Report Attendance.
            "jam_kerja_nyata": r.jam_kerja_nyata,
            "dikoreksi": bool(r.dikoreksi),
            "koreksi_alasan": r.koreksi_alasan,
            "koreksi_approver": r.koreksi_approver,
            "clock_out_hilang": bool(r.clock_out_hilang),
            "selisih_report": r.selisih_report,
        }
        for r in rows
    ]
    trend = [
        {"period": r.tanggal.isoformat(), "status": r.status,
         "kehadiran_pct": 100.0 if (r.hari_kerja and r.status in PRESENT) else None,
         "terlambat": 1 if (r.hari_kerja and r.terlambat) else 0}
        for r in rows
    ]
    first = rows[0]
    return {"nik": nik, "nama": first.nama, "departemen": first.departemen, "rekap": rekap, "range": range_items, "trend": trend}


def workdays_between(db: Session, d1: date, d2: date) -> int:
    """Jumlah hari kerja menurut kalender: Senin sampai Sabtu (LAST_WORKDAY), bukan hari libur yang terdaftar."""
    hols = set(db.scalars(select(Holiday.tanggal).where(Holiday.tanggal >= d1, Holiday.tanggal <= d2)))
    n, d = 0, d1
    while d <= d2:
        if d.isoweekday() <= settings.last_workday and d not in hols:
            n += 1
        d += timedelta(days=1)
    return n


def payroll_rows(db: Session, f: Filters) -> list[dict]:
    """Rekap per karyawan untuk payslip. Filter status diabaikan agar semua status ikut terhitung.

    hari_kerja_periode = hari kerja menurut kalender, hari_kerja_tercatat = hari kerja yang punya baris data,
    hari_tanpa_data = selisihnya (indikator kelengkapan data).

    Pembagi hari kerja (PRD F29) diatur HR (default 25). gaji_prorata_persen = hari_hadir_dibayar / pembagi * 100,
    dipakai sebagai masukan kolom "Gaji sesuai Hari Kerja" di rekap gaji (sistem tidak menghitung nominal).
    """
    from app.services.app_settings import payroll_workdays

    assert f.date_from and f.date_to
    expected = workdays_between(db, f.date_from, f.date_to)
    pembagi = payroll_workdays(db)
    out = []
    for r in recap(db, replace(f, status=None), "karyawan"):
        r["hari_hadir"] = r["hadir"] - r["dinas_luar"]  # hanya status Hadir, Dinas Luar dipisah
        r["hari_kerja_periode"] = expected
        r["hari_tanpa_data"] = max(0, expected - r["hari_kerja"])
        # Hari yang dibayar penuh: hadir + dinas luar + izin/sakit/cuti (kebijakan PRD: berizin tidak memotong).
        r["hari_dibayar"] = r["hari_hadir"] + r["dinas_luar"] + r["izin"] + r["sakit"] + r["cuti"]
        r["pembagi_hari_kerja"] = pembagi
        r["gaji_prorata_persen"] = round(r["hari_dibayar"] * 100 / pembagi, 2) if pembagi else None
        out.append(r)
    return out


def performance_ranking(db: Session, f: Filters, sort: str, order: str) -> list[dict]:
    """Peringkat kinerja per karyawan (PRD F26).

    Indikator per karyawan: tingkat kehadiran, ketepatan waktu (hadir tanpa terlambat),
    jam kerja, lembur, izin/sakit/cuti, alpa, dan koreksi. Dipakai halaman Kinerja Karyawan
    untuk HR, Audit, dan Sales/Marketing (bukan IT). Angka mengikuti definisi dashboard.
    """
    rows = recap(db, replace(f, status=None), "karyawan")
    out = []
    for r in rows:
        hadir_base = r["hadir"] - r["dinas_luar"]  # hanya Hadir yang dinilai ketepatan waktunya
        tepat_waktu_pct = round((hadir_base - r["terlambat"]) * 100 / hadir_base, 1) if hadir_base > 0 else None
        out.append({
            "nik": r["nik"],
            "nama": r["nama"],
            "departemen": r["departemen"],
            "hari_kerja": r["hari_kerja"],
            "hadir": r["hadir"],
            "dinas_luar": r["dinas_luar"],
            "kehadiran_pct": r["kehadiran_pct"],
            "terlambat": r["terlambat"],
            "menit_terlambat": r["menit_terlambat"],
            "tepat_waktu_pct": tepat_waktu_pct,
            "izin": r["izin"],
            "sakit": r["sakit"],
            "cuti": r["cuti"],
            "alpa": r["alpa"],
            "pulang_cepat": r["pulang_cepat"],
            "jam_kerja": r["jam_kerja"],
            "menit_lembur": r["menit_lembur"],
            "dikoreksi": r["dikoreksi"],
        })

    # Kolom pengurutan yang diizinkan. Nilai None selalu di bawah.
    keys = {
        "nama": lambda x: x["nama"],
        "departemen": lambda x: x["departemen"],
        "kehadiran_pct": lambda x: x["kehadiran_pct"],
        "tepat_waktu_pct": lambda x: x["tepat_waktu_pct"],
        "terlambat": lambda x: x["terlambat"],
        "menit_terlambat": lambda x: x["menit_terlambat"],
        "alpa": lambda x: x["alpa"],
        "jam_kerja": lambda x: x["jam_kerja"],
        "menit_lembur": lambda x: x["menit_lembur"],
    }
    key = keys.get(sort, keys["kehadiran_pct"])

    def sort_key(x):
        v = key(x)
        # Angka None (mis. kehadiran tanpa hari kerja) ditaruh paling bawah pada urutan menurun.
        if v is None:
            return (1, 0)
        return (0, v) if not isinstance(v, str) else (0, v)

    out.sort(key=sort_key, reverse=(order == "desc"))
    return out
