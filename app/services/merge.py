"""Penggabungan empat sumber menjadi Rekap Absensi terpadu (PRD bagian 4, F9).

Aturan prioritas saat sumber bertentangan:
1. Time Off disetujui menentukan status hari (Sakit/Cuti/Izin) dan menggugurkan keterlambatan.
2. Approval disetujui menimpa jam clock in/out dari Activity pada tanggal efektifnya.
3. Activity dipakai untuk hari tanpa koreksi (clock in paling awal, clock out paling akhir).
4. Jadwal shift dari Report Attendance; bila tidak ada, master shift (REGULER).
5. Report Attendance tidak menimpa hasil sistem; dipakai sebagai pembanding (selisih ditandai).

Output: list of dict dengan kolom yang cocok untuk tabel `attendance` (termasuk kolom terpadu).
"""
from collections import defaultdict
from datetime import date, timedelta

from app.core.config import settings
from app.services.rules import DEFAULT_END, DEFAULT_START, _end_adjusted

# Pemetaan Policy Code Time Off ke status (PRD bagian 6). Kode lain dianggap Izin.
STATUS_BY_POLICY = {"S": "Sakit", "SAKIT": "Sakit", "C": "Cuti", "CUTI": "Cuti", "I": "Izin", "IZIN": "Izin"}
PRESENT = ("Hadir", "Dinas Luar")


def _is_approved(status: str | None) -> bool:
    return (status or "").strip().lower() in ("approved", "approve", "disetujui", "accepted")


def _policy_status(policy_code: str | None, policy_name: str | None) -> str:
    for key in ((policy_code or "").strip().upper(), (policy_name or "").strip().upper()):
        if key in STATUS_BY_POLICY:
            return STATUS_BY_POLICY[key]
    return "Izin"


def _workday(d: date, holidays: set[date]) -> bool:
    return d.isoweekday() <= settings.last_workday and d not in holidays


def merge(
    *,
    activity: list[dict],
    report: list[dict],
    timeoff: list[dict],
    approval: list[dict],
    rules: dict[str, tuple[int, int]],
    holidays: set[date],
    tanggal_awal: date,
    tanggal_akhir: date,
) -> list[dict]:
    """Bangun Rekap Absensi terpadu untuk rentang tanggal tertentu."""
    # ---------- Indeks per (employee_id, tanggal) ----------
    ketukan: dict[tuple[str, date], list[dict]] = defaultdict(list)
    for a in activity:
        if a.get("waktu_menit") is not None:
            ketukan[(a["employee_id"], a["tanggal"])].append(a)

    report_idx: dict[tuple[str, date], dict] = {(r["employee_id"], r["tanggal"]): r for r in report}

    # Time Off disetujui -> status per tanggal (expand rentang mulai..selesai).
    izin_idx: dict[tuple[str, date], dict] = {}
    for t in timeoff:
        if not _is_approved(t.get("status")):
            continue
        mulai = t["tanggal_mulai"]
        selesai = t.get("tanggal_selesai") or mulai
        d = mulai
        while d <= selesai:
            izin_idx[(t["employee_id"], d)] = t
            d += timedelta(days=1)

    # Koreksi disetujui -> jam pengganti per tanggal efektif.
    koreksi_idx: dict[tuple[str, date], dict] = {}
    for c in approval:
        if _is_approved(c.get("status")):
            koreksi_idx[(c["employee_id"], c["tanggal_efektif"])] = c

    # ---------- Kumpulan kunci yang muncul di sumber mana pun ----------
    keys: set[tuple[str, date]] = set()
    keys |= set(ketukan.keys())
    keys |= set(report_idx.keys())
    keys |= set(izin_idx.keys())
    keys |= set(koreksi_idx.keys())

    # Nama & organisasi/cabang dari Report bila ada.
    ident: dict[str, dict] = {}
    for r in report:
        info = ident.setdefault(r["employee_id"], {"nama": r.get("nama") or r["employee_id"],
                                                   "organisasi": r.get("organisasi"), "cabang": r.get("cabang")})

    rows: list[dict] = []
    for eid, d in sorted(keys):
        if d < tanggal_awal or d > tanggal_akhir:
            continue
        rep = report_idx.get((eid, d))
        taps = ketukan.get((eid, d), [])
        izin = izin_idx.get((eid, d))
        kor = koreksi_idx.get((eid, d))

        # ---- Jadwal shift ----
        shift = None
        start, end = DEFAULT_START, DEFAULT_END
        if rep and rep.get("shift_name"):
            shift = rep["shift_name"]
            if rep.get("jadwal_masuk_menit") is not None:
                start = rep["jadwal_masuk_menit"]
            if rep.get("jadwal_pulang_menit") is not None:
                end = _end_adjusted(start, rep["jadwal_pulang_menit"])
        shift = shift or "REGULER"
        if shift in rules:
            start, end = rules[shift]

        workday = _workday(d, holidays)

        # ---- Ketukan Activity: clock in paling awal, clock out paling akhir ----
        ins = [t["waktu_menit"] for t in taps if (t.get("tipe") or "").lower().replace("_", " ") in ("clock in", "in") or (t.get("tipe") or "").lower() == "clock_in"]
        outs = [t["waktu_menit"] for t in taps if (t.get("tipe") or "").lower().replace("_", " ") in ("clock out", "out") or (t.get("tipe") or "").lower() == "clock_out"]
        # Bila tipe tidak dikenali, tebak dari posisi (jam masuk = paling awal).
        if not ins and not outs and taps:
            menits = sorted(t["waktu_menit"] for t in taps)
            ins, outs = [menits[0]], [menits[-1]] if len(menits) > 1 else []
        jam_masuk = min(ins) if ins else None
        jam_keluar = max(outs) if outs else None
        jumlah_ketukan = len(taps)
        lokasi = next((t.get("lokasi") for t in taps if t.get("lokasi")), None)

        sumber = []
        if taps:
            sumber.append("activity")

        # ---- Approval menimpa jam ----
        dikoreksi = False
        koreksi_alasan = koreksi_approver = None
        koreksi_setelah_tutup = False
        if kor:
            if kor.get("jam_masuk_menit") is not None:
                jam_masuk = kor["jam_masuk_menit"]
            if kor.get("jam_keluar_menit") is not None:
                jam_keluar = kor["jam_keluar_menit"]
            dikoreksi = True
            koreksi_alasan = kor.get("alasan")
            koreksi_approver = kor.get("approver")
            sumber.append("approval")
            # Koreksi yang diajukan setelah akhir bulan efektif.
            diajukan = kor.get("diajukan_pada")
            if diajukan and (diajukan.year, diajukan.month) > (d.year, d.month):
                koreksi_setelah_tutup = True

        # ---- Status akhir ----
        clock_out_hilang = jam_masuk is not None and jam_keluar is None
        clock_in_hilang = jam_keluar is not None and jam_masuk is None

        if izin:
            status = _policy_status(izin.get("policy_code"), izin.get("policy_name"))
            sumber.append("timeoff")
        elif jam_masuk is not None:
            status = "Hadir"
        elif not workday:
            status = "Libur"
        else:
            status = "Alpa"

        # Kode kehadiran Report sebagai status_file bila ada.
        status_file = (rep or {}).get("kode_kehadiran") or None

        # ---- Hitung keterlambatan, pulang cepat, lembur, durasi ----
        keluar_adj = jam_keluar
        if jam_masuk is not None and jam_keluar is not None and end >= 1440 and jam_keluar < jam_masuk:
            keluar_adj = jam_keluar + 1440

        menit_terlambat = 0
        menit_pulang_cepat = 0
        menit_lembur = 0
        durasi = None
        jam_kerja_nyata = None

        if status in PRESENT and workday and jam_masuk is not None:
            menit_terlambat = max(0, jam_masuk - start)
        if status in PRESENT and workday and keluar_adj is not None:
            menit_pulang_cepat = max(0, end - keluar_adj)
        # Izin menggugurkan keterlambatan.
        if izin:
            menit_terlambat = 0
            menit_pulang_cepat = 0
        if jam_masuk is not None and keluar_adj is not None:
            durasi = round((keluar_adj - jam_masuk) / 60, 2)
            jam_kerja_nyata = durasi
        if status in PRESENT and keluar_adj is not None:
            menit_lembur = max(0, keluar_adj - end)

        # ---- Pembanding Report Attendance (tidak menimpa) ----
        selisih = None
        if rep:
            bagian = []
            if rep.get("late_in_menit") is not None and rep["late_in_menit"] != menit_terlambat:
                bagian.append(f"terlambat sistem {menit_terlambat} mnt vs report {rep['late_in_menit']} mnt")
            if rep.get("real_hour") is not None and jam_kerja_nyata is not None and abs(rep["real_hour"] - jam_kerja_nyata) >= 0.1:
                bagian.append(f"jam nyata sistem {jam_kerja_nyata} vs report {rep['real_hour']}")
            if bagian:
                selisih = "; ".join(bagian)

        info = ident.get(eid, {"nama": eid, "organisasi": None, "cabang": None})
        nama = (rep or {}).get("nama") or info["nama"]
        departemen = (rep or {}).get("organisasi") or info.get("organisasi") or "Tanpa divisi"

        rows.append({
            "nik": eid,
            "nama": nama,
            "tanggal": d,
            "periode_ym": d.strftime("%Y-%m"),
            "departemen": departemen,
            "shift": shift,
            "jam_masuk_menit": jam_masuk,
            "jam_keluar_menit": jam_keluar,
            "status_file": status_file,
            "status": status,
            "keterangan": izin.get("policy_name") if izin else (kor.get("alasan") if kor else None),
            "lokasi_kerja": lokasi,
            "hari_kerja": workday,
            "menit_terlambat": menit_terlambat,
            "terlambat": menit_terlambat > 0,
            "menit_pulang_cepat": menit_pulang_cepat,
            "durasi_kerja": durasi,
            "menit_lembur": menit_lembur,
            # Kolom terpadu
            "jam_kerja_nyata": jam_kerja_nyata,
            "jumlah_ketukan": jumlah_ketukan or None,
            "clock_out_hilang": clock_out_hilang,
            "clock_in_hilang": clock_in_hilang,
            "sumber": "+".join(dict.fromkeys(sumber)) or None,
            "dikoreksi": dikoreksi,
            "koreksi_alasan": koreksi_alasan,
            "koreksi_approver": koreksi_approver,
            "koreksi_setelah_tutup": koreksi_setelah_tutup,
            "izin_kode": izin.get("policy_code") if izin else None,
            "izin_nama": izin.get("policy_name") if izin else None,
            "izin_tipe": izin.get("tipe") if izin else None,
            "selisih_report": selisih,
            # Sama dengan status_file: jangan diisi kode kehadiran Report, karena
            # attendance.status_file menyimpan kode asli dan rekap dihitung ulang dari data mentah.
            "status_report": None,
        })
    return rows


def build_employee_master(report: list[dict], activity: list[dict]) -> list[dict]:
    """Master karyawan dari Report (utama) dan Activity (pelengkap). PRD F15."""
    out: dict[str, dict] = {}
    for r in report:
        eid = r["employee_id"]
        cabang = r.get("cabang")
        out[eid] = {
            "employee_id": eid,
            "nama": r.get("nama") or eid,
            "jabatan": None,
            "organisasi": r.get("organisasi"),
            "cabang": cabang,
            "status_kerja": r.get("status_kerja"),
            "resign": bool(cabang and "resigned" in str(cabang).lower()),
        }
    for a in activity:
        eid = a["employee_id"]
        if eid not in out:
            out[eid] = {"employee_id": eid, "nama": eid, "jabatan": None, "organisasi": None,
                        "cabang": None, "status_kerja": None, "resign": False}
    return list(out.values())
