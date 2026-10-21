from datetime import date

import polars as pl

from app.core.config import settings
from app.services.rules import DEFAULT_END, DEFAULT_START

I64 = pl.Int64
DB_COLUMNS = [
    "nik", "nama", "tanggal", "periode_ym", "departemen", "shift", "jam_masuk_menit", "jam_keluar_menit",
    "status_file", "status", "keterangan", "lokasi_kerja", "hari_kerja", "menit_terlambat", "terlambat",
    "menit_pulang_cepat", "durasi_kerja", "menit_lembur",
    # Kolom Rekap Absensi terpadu tidak dihitung ulang di sini; disertakan agar DataFrame hasil compute
    # tetap punya bentuk yang sama dan insert ke tabel attendance tidak gagal karena kolom hilang.
    "jam_kerja_nyata", "jumlah_ketukan", "clock_out_hilang", "clock_in_hilang", "sumber", "dikoreksi",
    "koreksi_alasan", "koreksi_approver", "koreksi_setelah_tutup", "izin_kode", "izin_nama", "izin_tipe",
    "selisih_report", "status_report",
]


def compute(df: pl.DataFrame, rules: pl.DataFrame, holidays: list[date]) -> pl.DataFrame:
    """Hitung status akhir, keterlambatan, pulang cepat, durasi, dan lembur.

    Kolom masukan: nik, nama, tanggal, departemen, shift, jam_masuk_menit, jam_keluar_menit,
    status_in (status dari file atau kosong), keterangan, lokasi_kerja.
    Aturan (PRD bagian 7):
      terlambat    = max(0, jam masuk - jam mulai - toleransi), hanya status Hadir pada hari kerja
      pulang cepat = max(0, jam selesai - jam keluar), hanya pada hari kerja
      lembur       = max(0, jam keluar - jam selesai)
      durasi       = jam keluar - jam masuk (jam)
      hari kerja   = Senin sampai Sabtu (LAST_WORKDAY), bukan hari libur terdaftar
    """
    hol = pl.DataFrame({"tanggal": holidays, "_hol": [True] * len(holidays)}, schema={"tanggal": pl.Date, "_hol": pl.Boolean})
    d = (
        df.with_row_index("_i")
        .join(rules, left_on="shift", right_on="kode", how="left")
        .join(hol, on="tanggal", how="left")
        .sort("_i")
    )

    masuk, keluar = pl.col("jam_masuk_menit"), pl.col("jam_keluar_menit")
    d = d.with_columns(
        workday=(pl.col("tanggal").dt.weekday() <= settings.last_workday) & ~pl.col("_hol").fill_null(False),
        start=pl.col("start_min").fill_null(DEFAULT_START),
        end=pl.col("end_min").fill_null(DEFAULT_END),
        tol=pl.col("tol").fill_null(0),
    )
    d = d.with_columns(
        keluar_adj=pl.when(masuk.is_not_null() & keluar.is_not_null() & (pl.col("end") >= 1440) & (keluar < masuk))
        .then(keluar + 1440)
        .otherwise(keluar),
        status_base=pl.col("status_in").fill_null(""),
    )
    base = pl.col("status_base")
    d = d.with_columns(
        status=pl.when(base == "")
        .then(pl.when(masuk.is_not_null()).then(pl.lit("Hadir")).when(pl.col("workday")).then(pl.lit("Alpa")).otherwise(pl.lit("Libur")))
        .when((base == "Alpa") & ~pl.col("workday"))
        .then(pl.lit("Libur"))
        .otherwise(base)
    )
    zero = pl.lit(0, dtype=I64)
    hadir = pl.col("status") == "Hadir"
    work = pl.col("workday")
    late = pl.when(hadir & work & masuk.is_not_null()).then(pl.max_horizontal(zero, masuk - pl.col("start") - pl.col("tol"))).otherwise(zero)
    early = pl.when(hadir & work & pl.col("keluar_adj").is_not_null()).then(pl.max_horizontal(zero, pl.col("end") - pl.col("keluar_adj"))).otherwise(zero)
    over = pl.when(hadir & pl.col("keluar_adj").is_not_null()).then(pl.max_horizontal(zero, pl.col("keluar_adj") - pl.col("end"))).otherwise(zero)
    dur = pl.when(masuk.is_not_null() & pl.col("keluar_adj").is_not_null()).then(((pl.col("keluar_adj") - masuk) / 60).round(2)).otherwise(None)
    d = d.with_columns(
        menit_terlambat=late,
        menit_pulang_cepat=early,
        menit_lembur=over,
        durasi_kerja=dur,
        hari_kerja=pl.col("workday"),
        periode_ym=pl.col("tanggal").dt.strftime("%Y-%m"),
        status_file=pl.col("status_in"),
    ).with_columns(terlambat=pl.col("menit_terlambat") > 0)

    # Kolom Rekap Absensi terpadu tidak berasal dari file template lama: isi dengan nilai kosong/default.
    null_i64 = pl.lit(None, dtype=pl.Int64)
    null_f64 = pl.lit(None, dtype=pl.Float64)
    null_str = pl.lit(None, dtype=pl.Utf8)
    d = d.with_columns(
        jam_kerja_nyata=null_f64,
        jumlah_ketukan=null_i64,
        clock_out_hilang=pl.lit(False, dtype=pl.Boolean),
        clock_in_hilang=pl.lit(False, dtype=pl.Boolean),
        sumber=null_str,
        dikoreksi=pl.lit(False, dtype=pl.Boolean),
        koreksi_alasan=null_str,
        koreksi_approver=null_str,
        koreksi_setelah_tutup=pl.lit(False, dtype=pl.Boolean),
        izin_kode=null_str,
        izin_nama=null_str,
        izin_tipe=null_str,
        selisih_report=null_str,
        status_report=null_str,
    )
    return d.select(DB_COLUMNS)
