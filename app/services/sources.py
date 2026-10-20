"""Parser empat sumber data presensi (PRD bagian 3, F4-F8).

Setiap fungsi membaca satu file Excel/CSV menjadi DataFrame ternormalisasi. Nama kolom sumber
berbeda-beda, jadi dipetakan lewat ALIASES. Parser mengembalikan data bersih + daftar peringatan
dan tidak menyentuh database; penyimpanan dilakukan router.

Format yang diasumsikan (dokumentasi PRD bagian 3):
- Activity: Employee ID, Date, Time (atau Date Time), Type (clock in/out), Location, Shift Name.
- Report Attendance: Employee ID, Name, Date, Shift Name, Schedule In/Out, Check In/Out, Late In,
  Early Out, Actual Working Hour, Real Working Hour, Attendance Code, Organization, Branch.
  Baris "TOTAL FOR EMPLOYEE" dan "GRAND TOTAL" dilewati.
- Time Off: Employee ID, Request Id, Start Date, End Date, Policy Code, Policy Name, Time Off Type,
  Duration, Request Status.
- Approval: Employee ID, Date (tanggal efektif), Description, Notes, Request Status, Approver.
  Jam diurai dari Description: "check in at ... and check out at ...".
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import polars as pl

# ---------- Pemetaan kolom ----------

ACTIVITY_ALIASES = {
    "employee_id": "employee_id", "employee id": "employee_id", "nik": "employee_id", "nip": "employee_id", "id": "employee_id",
    "date": "tanggal", "tanggal": "tanggal",
    "time": "waktu", "jam": "waktu", "datetime": "waktu", "date time": "waktu", "attendance time": "waktu",
    "type": "tipe", "tipe": "tipe", "attendance type": "tipe",
    "location setting name": "lokasi", "location": "lokasi", "lokasi": "lokasi",
    "shift name": "shift_name", "shift": "shift_name",
    "status": "status",
}

REPORT_ALIASES = {
    "employee id": "employee_id", "employee_id": "employee_id", "nik": "employee_id", "nip": "employee_id",
    "employee name": "nama", "name": "nama", "nama": "nama",
    "date": "tanggal", "tanggal": "tanggal",
    "shift name": "shift_name", "shift": "shift_name",
    "schedule in": "jadwal_masuk", "schedule start": "jadwal_masuk", "jadwal masuk": "jadwal_masuk",
    "schedule out": "jadwal_pulang", "schedule end": "jadwal_pulang", "jadwal pulang": "jadwal_pulang",
    "check in": "check_in", "clock in": "check_in",
    "check out": "check_out", "clock out": "check_out",
    "late in": "late_in", "early out": "early_out",
    "actual working hour": "actual_hour", "real working hour": "real_hour",
    "attendance code": "kode_kehadiran", "code": "kode_kehadiran",
    "organization": "organisasi", "department": "organisasi", "divisi": "organisasi",
    "branch": "cabang", "cabang": "cabang",
    "status": "status_kerja",
}

TIMEOFF_ALIASES = {
    "employee id": "employee_id", "employee_id": "employee_id", "nik": "employee_id",
    "request id": "request_id", "request_id": "request_id", "id": "request_id",
    "start date": "tanggal_mulai", "start_date": "tanggal_mulai", "tanggal mulai": "tanggal_mulai", "from date": "tanggal_mulai",
    "end date": "tanggal_selesai", "end_date": "tanggal_selesai", "tanggal selesai": "tanggal_selesai", "to date": "tanggal_selesai",
    "policy code": "policy_code", "policy_code": "policy_code", "kode kebijakan": "policy_code",
    "policy name": "policy_name", "policy_name": "policy_name", "nama kebijakan": "policy_name",
    "time off type": "tipe", "time_off_type": "tipe", "type": "tipe", "tipe": "tipe",
    "duration": "durasi", "durasi": "durasi",
    "request status": "status", "status": "status",
}

APPROVAL_ALIASES = {
    "employee id": "employee_id", "employee_id": "employee_id", "nik": "employee_id",
    "date": "tanggal", "tanggal": "tanggal", "effective date": "tanggal",
    "description": "description", "keterangan": "description",
    "notes": "notes", "note": "notes", "alasan": "notes", "catatan": "notes",
    "request status": "status", "status": "status",
    "approver": "approver", "approval by": "approver", "approved by": "approver",
    "request date": "diajukan", "created at": "diajukan", "created": "diajukan", "diajukan": "diajukan",
}

# Kata kunci untuk mengelompokkan alasan Approval (PRD F8).
REASON_KEYWORDS = [
    ("lupa absen", ["lupa", "forget", "no checkin", "no checkout", "tidak absen"]),
    ("WFH", ["wfh", "work from home", "kerja dari rumah", "remote"]),
    ("sakit", ["sakit", "ill", "sick", "demam"]),
    ("error aplikasi", ["error", "gangguan", "bug", "aplikasi"]),
    ("karyawan baru", ["new join", "baru", "new employee"]),
    ("transportasi", ["commuter", "transport", "macet", "kendala perjalanan"]),
]


class SourceError(ValueError):
    """File sumber tidak bisa diurai (kolom wajib hilang, sheet salah, dsb)."""


@dataclass
class Parsed:
    kind: str
    rows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mapped_columns: list[str] = field(default_factory=list)
    date_from: date | None = None
    date_to: date | None = None

    @property
    def row_count(self) -> int:
        return len(self.rows)


# ---------- Utilitas ----------

def _norm(name: str) -> str:
    n = str(name).strip().lstrip("\ufeff").lower()
    n = re.sub(r"[\s\-/]+", " ", n)
    return re.sub(r"[^a-z0-9 ]", "", n)


def _read_raw(path: Path, sheet: str | None = None) -> pl.DataFrame:
    ext = path.suffix.lower()
    try:
        if ext == ".csv":
            return pl.read_csv(path, infer_schema=False, encoding="utf8-lossy", truncate_ragged_lines=True)
        if ext in (".xlsx", ".xls"):
            kwargs = {"engine": "calamine", "infer_schema_length": 0}
            if sheet:
                kwargs["sheet_name"] = sheet
            return pl.read_excel(path, **kwargs)
    except Exception as e:  # noqa: BLE001
        raise SourceError(f"File tidak dapat dibaca: {e}")
    raise SourceError("Format file harus CSV atau XLSX")


def _map_columns(df: pl.DataFrame, aliases: dict[str, str], required: list[str], kind: str) -> tuple[pl.DataFrame, list[str]]:
    mapping: dict[str, str] = {}
    used: set[str] = set()
    info: list[str] = []
    for col in df.columns:
        norm = _norm(col)
        target = aliases.get(norm, norm.replace(" ", "_"))
        if target in used:
            target = f"_dup_{col}"
        used.add(target)
        mapping[col] = target
        if target != norm.replace(" ", "_"):
            info.append(f"{str(col).strip()} dibaca sebagai {target}")
    df = df.rename(mapping)
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise SourceError(f"Kolom wajib {kind} tidak ditemukan: {', '.join(missing)}")
    return df.with_columns(pl.all().cast(pl.Utf8)), info


def _to_date(v) -> date | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "nan", "null"):
        return None
    # Ambil bagian tanggal bila ada waktu.
    s = s.split(" ")[0].split("T")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _to_minutes(v) -> int | None:
    """Ubah jam ke menit sejak 00:00. Menerima 'HH:MM', 'HH:MM:SS', 'YYYY-MM-DD HH:MM', atau pecahan hari Excel."""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() in ("none", "nan", "null", ""):
        return None
    # Waktu lengkap dengan tanggal: ambil jam terakhir.
    m = re.search(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", s)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h * 60 + mi
        return None
    # Pecahan hari (0.3333 = 08:00).
    try:
        f = float(s)
        if 0 <= f < 1:
            return int(round(f * 1440))
    except ValueError:
        pass
    return None


def _norm_shift(name: str | None) -> str | None:
    """Normalisasi nama shift: trim dan hapus titik di belakang (LSP-2. -> LSP-2)."""
    if not name:
        return None
    n = str(name).strip().rstrip(".").strip()
    return n or None


def _split_hhmm(text: str) -> tuple[int | None, int | None]:
    """Ambil jam masuk dan keluar dari teks Description Approval.

    Format contoh: "for check in at 08:00 and check out at 16:30", atau salah satu sisi
    "no checkin"/"no checkout". Bila hanya ada satu jam tanpa penanda sisi, anggap itu jam keluar
    (kasus paling umum: lupa absen pulang).
    """
    t = (text or "").lower()
    masuk = keluar = None

    def _m(pattern: str) -> int | None:
        m = re.search(pattern + r"\s*(?:at\s*)?(\d{1,2}):(\d{2})", t)
        if not m:
            return None
        h, mi = int(m.group(1)), int(m.group(2))
        return h * 60 + mi if (0 <= h <= 23 and 0 <= mi <= 59) else None

    masuk = _m(r"check\s*in")
    keluar = _m(r"check\s*out")

    if "no checkin" in t or "no check in" in t:
        masuk = None
    if "no checkout" in t or "no check out" in t:
        keluar = None

    if masuk is None and keluar is None:
        # Tidak ada penanda sisi: ambil jam pertama yang muncul.
        found = re.findall(r"(\d{1,2}):(\d{2})", t)
        if found:
            h, mi = int(found[0][0]), int(found[0][1])
            if 0 <= h <= 23 and 0 <= mi <= 59:
                keluar = h * 60 + mi
    return masuk, keluar


def _categorize_reason(text: str | None) -> str:
    t = (text or "").lower()
    for label, keys in REASON_KEYWORDS:
        if any(k in t for k in keys):
            return label
    return "lainnya"


# ---------- Parser tiap sumber ----------

def parse_activity(path: Path) -> Parsed:
    df = _read_raw(path)
    df, mapped = _map_columns(df, ACTIVITY_ALIASES, ["employee_id", "tanggal", "waktu"], "Activity")
    out = Parsed("activity", mapped_columns=mapped)
    dates: list[date] = []
    for r in df.iter_rows(named=True):
        d = _to_date(r.get("tanggal"))
        if d is None:
            # Bisa jadi tanggal dan waktu menyatu di kolom waktu.
            d = _to_date(r.get("waktu"))
        if d is None:
            continue
        menit = _to_minutes(r.get("waktu"))
        tipe = (r.get("tipe") or "").strip().lower()
        if not tipe:
            tipe = "clock_in" if "in" in str(r.get("waktu", "")).lower() else None
        out.rows.append({
            "employee_id": (r.get("employee_id") or "").strip(),
            "tanggal": d,
            "waktu_menit": menit,
            "tipe": tipe or None,
            "lokasi": (r.get("lokasi") or None),
            "shift_name": _norm_shift(r.get("shift_name")),
            "status": (r.get("status") or None),
            "raw": {k: v for k, v in r.items() if v is not None},
        })
        dates.append(d)
    if dates:
        out.date_from, out.date_to = min(dates), max(dates)
    if not out.rows:
        out.warnings.append("Activity tidak berisi baris yang bisa dibaca")
    return out


def _skip_report_row(employee_id: str, nama: str | None) -> bool:
    joined = f"{employee_id or ''} {nama or ''}".upper()
    return "TOTAL" in joined or "GRAND TOTAL" in joined


def parse_report(path: Path) -> Parsed:
    # Sheet bernama "Export Attendance Report" pada contoh PRD; fallback ke sheet pertama.
    df = None
    for sheet in ("Export Attendance Report", None):
        try:
            df = _read_raw(path, sheet)
            if df is not None and df.height:
                break
        except Exception:  # noqa: BLE001
            df = None
    if df is None:
        raise SourceError("File Report Attendance tidak dapat dibaca")
    df, mapped = _map_columns(df, REPORT_ALIASES, ["employee_id", "tanggal"], "Report Attendance")
    out = Parsed("report", mapped_columns=mapped)
    dates: list[date] = []
    skipped = 0
    for r in df.iter_rows(named=True):
        eid = (r.get("employee_id") or "").strip()
        nama = (r.get("nama") or "").strip() or None
        if _skip_report_row(eid, nama):
            skipped += 1
            continue
        d = _to_date(r.get("tanggal"))
        if not eid or d is None:
            continue
        out.rows.append({
            "employee_id": eid,
            "nama": nama,
            "tanggal": d,
            "shift_name": _norm_shift(r.get("shift_name")),
            "jadwal_masuk_menit": _to_minutes(r.get("jadwal_masuk")),
            "jadwal_pulang_menit": _to_minutes(r.get("jadwal_pulang")),
            "check_in_menit": _to_minutes(r.get("check_in")),
            "check_out_menit": _to_minutes(r.get("check_out")),
            "late_in_menit": _to_minutes(r.get("late_in")),
            "early_out_menit": _to_minutes(r.get("early_out")),
            "actual_hour": _to_float(r.get("actual_hour")),
            "real_hour": _to_float(r.get("real_hour")),
            "kode_kehadiran": (r.get("kode_kehadiran") or None),
            "organisasi": (r.get("organisasi") or None),
            "cabang": (r.get("cabang") or None),
            "raw": {k: v for k, v in r.items() if v is not None},
        })
        dates.append(d)
    if skipped:
        out.warnings.append(f"{skipped} baris subtotal/GRAND TOTAL dilewati")
    if dates:
        out.date_from, out.date_to = min(dates), max(dates)
    if not out.rows:
        out.warnings.append("Report Attendance tidak berisi baris data")
    return out


def _to_float(v) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", ".")
    if not s or s.lower() in ("none", "nan", "null"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_timeoff(path: Path) -> Parsed:
    df = _read_raw(path)
    df, mapped = _map_columns(df, TIMEOFF_ALIASES, ["employee_id", "tanggal_mulai"], "Time Off")
    out = Parsed("timeoff", mapped_columns=mapped)
    dates: list[date] = []
    for r in df.iter_rows(named=True):
        eid = (r.get("employee_id") or "").strip()
        mulai = _to_date(r.get("tanggal_mulai"))
        if not eid or mulai is None:
            continue
        selesai = _to_date(r.get("tanggal_selesai")) or mulai
        out.rows.append({
            "employee_id": eid,
            "request_id": (r.get("request_id") or None),
            "tanggal_mulai": mulai,
            "tanggal_selesai": selesai,
            "policy_code": (r.get("policy_code") or None),
            "policy_name": (r.get("policy_name") or None),
            "tipe": (r.get("tipe") or None),
            "durasi": _to_float(r.get("durasi")),
            "status": (r.get("status") or None),
            "raw": {k: v for k, v in r.items() if v is not None},
        })
        dates.extend([mulai, selesai])
    if dates:
        out.date_from, out.date_to = min(dates), max(dates)
    if not out.rows:
        out.warnings.append("Time Off tidak berisi baris data (hanya header?)")
    return out


def parse_approval(path: Path) -> Parsed:
    df = _read_raw(path)
    df, mapped = _map_columns(df, APPROVAL_ALIASES, ["employee_id", "tanggal"], "Approval")
    out = Parsed("approval", mapped_columns=mapped)
    dates: list[date] = []
    unparsed = 0
    for r in df.iter_rows(named=True):
        eid = (r.get("employee_id") or "").strip()
        d = _to_date(r.get("tanggal"))
        if not eid or d is None:
            continue
        desc = r.get("description") or ""
        masuk, keluar = _split_hhmm(desc)
        if masuk is None and keluar is None:
            unparsed += 1
        notes = r.get("notes")
        out.rows.append({
            "employee_id": eid,
            "tanggal_efektif": d,
            "jam_masuk_menit": masuk,
            "jam_keluar_menit": keluar,
            "alasan": notes,
            "alasan_kategori": _categorize_reason(notes),
            "status": (r.get("status") or None),
            "approver": (r.get("approver") or None),
            "diajukan_pada": _to_date(r.get("diajukan")),
            "raw": {k: v for k, v in r.items() if v is not None},
        })
        dates.append(d)
    if unparsed:
        out.warnings.append(f"{unparsed} baris Approval: jam tidak bisa diurai dari Description, perlu ditinjau manual")
    if dates:
        out.date_from, out.date_to = min(dates), max(dates)
    if not out.rows:
        out.warnings.append("Approval tidak berisi baris data")
    return out


PARSERS = {
    "activity": parse_activity,
    "report": parse_report,
    "timeoff": parse_timeoff,
    "approval": parse_approval,
}


def parse_source(kind: str, path: Path) -> Parsed:
    if kind not in PARSERS:
        raise SourceError(f"Jenis sumber tidak dikenal: {kind}")
    return PARSERS[kind](path)
