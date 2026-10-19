import re
from pathlib import Path

import polars as pl

# Pemetaan nama kolom sederhana untuk file mesin absen atau HRIS yang headernya berbeda.
ALIASES = {
    "nip": "nik", "no_induk": "nik", "nomor_induk": "nik", "employee_id": "nik", "id_karyawan": "nik", "id": "nik",
    "nama_karyawan": "nama", "name": "nama", "employee_name": "nama",
    "tgl": "tanggal", "date": "tanggal", "tanggal_absen": "tanggal", "tanggal_presensi": "tanggal",
    "divisi": "departemen", "unit": "departemen", "unit_kerja": "departemen", "department": "departemen", "dept": "departemen",
    "kode_shift": "shift",
    "jam_datang": "jam_masuk", "check_in": "jam_masuk", "clock_in": "jam_masuk", "masuk": "jam_masuk", "in": "jam_masuk",
    "jam_pulang": "jam_keluar", "check_out": "jam_keluar", "clock_out": "jam_keluar", "keluar": "jam_keluar", "pulang": "jam_keluar", "out": "jam_keluar",
    "kehadiran": "status", "status_kehadiran": "status", "status_presensi": "status",
    "ket": "keterangan", "alasan": "keterangan", "catatan": "keterangan",
    "lokasi": "lokasi_kerja", "work_location": "lokasi_kerja",
}


def _sniff_separator(path: Path) -> str:
    """CSV dari Excel versi Indonesia sering memakai titik koma."""
    with open(path, "rb") as f:
        head = f.readline().decode("utf-8-sig", errors="replace")
    return ";" if head.count(";") > head.count(",") else ","


def _normalize(name: str) -> str:
    n = name.strip().lstrip("\ufeff").lower()
    n = re.sub(r"[\s\-/]+", "_", n)
    return re.sub(r"[^a-z0-9_]", "", n)


def read_table(path: Path) -> tuple[pl.DataFrame, list[str]]:
    """Baca CSV atau XLSX sebagai teks. Mengembalikan data dan daftar pemetaan kolom otomatis."""
    ext = path.suffix.lower()
    if ext == ".csv":
        df = pl.read_csv(path, separator=_sniff_separator(path), infer_schema=False, encoding="utf8-lossy")
    elif ext == ".xlsx":
        df = pl.read_excel(path, engine="calamine", infer_schema_length=0)
    else:
        raise ValueError("Format file tidak didukung")

    mapping: dict[str, str] = {}
    used: set[str] = set()
    info: list[str] = []
    for i, original in enumerate(df.columns):
        norm = _normalize(original)
        target = ALIASES.get(norm, norm)
        if not target or target in used:  # kolom ganda atau tanpa nama dibiarkan
            target = f"_kolom_{i}"
        used.add(target)
        mapping[original] = target
        if ALIASES.get(norm) and target != norm:
            info.append(f"{original.strip()} dibaca sebagai {target}")
    df = df.rename(mapping)
    return df.with_columns(pl.all().cast(pl.Utf8)), info
