from dataclasses import dataclass, field

import polars as pl

from app.services.rules import DEFAULT_SHIFT

REQUIRED = ["nik", "nama", "tanggal", "jam_masuk", "jam_keluar", "status"]
OPTIONAL = ["departemen", "shift", "keterangan", "lokasi_kerja"]
ALL = REQUIRED + OPTIONAL
MAX_ERRORS = 200
STATUS_HELP = "Hadir, Izin, Sakit, Cuti, Dinas Luar, atau Alpa"
STATUS_MAP = {
    "hadir": "Hadir", "izin": "Izin", "sakit": "Sakit", "cuti": "Cuti",
    "dinas luar": "Dinas Luar", "dinas_luar": "Dinas Luar", "dl": "Dinas Luar", "alpa": "Alpa", "alpha": "Alpa",
}


@dataclass
class ValidationResult:
    row_count: int = 0
    error_count: int = 0
    errors: list[dict] = field(default_factory=list)
    preview: list[dict] = field(default_factory=list)
    clean: pl.DataFrame | None = None  # terisi hanya jika lolos validasi


def _blank_to_null(c: str) -> pl.Expr:
    s = pl.col(c).str.strip_chars()
    return pl.when(s.str.len_chars() == 0).then(None).otherwise(s).alias(c)


def _minutes(col: str) -> pl.Expr:
    h = pl.col(col).str.extract(r"(\d{1,2}):(\d{2})", 1).cast(pl.Int64, strict=False)
    m = pl.col(col).str.extract(r"(\d{1,2}):(\d{2})", 2).cast(pl.Int64, strict=False)
    return pl.when((h <= 23) & (m <= 59)).then(h * 60 + m).otherwise(None)


def validate(df: pl.DataFrame, rules: dict[str, tuple[int, int]]) -> ValidationResult:
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        err = {"row": 1, "column": ", ".join(missing), "message": "Kolom wajib tidak ditemukan di header"}
        return ValidationResult(error_count=1, errors=[err])

    present = [c for c in ALL if c in df.columns]
    for c in OPTIONAL:
        if c not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(c))
    df = df.select(ALL).with_row_index("idx").with_columns([_blank_to_null(c) for c in ALL])
    df = df.filter(~pl.all_horizontal([pl.col(c).is_null() for c in ALL]))  # baris kosong diabaikan
    if df.height == 0:
        return ValidationResult(error_count=1, errors=[{"row": 2, "column": "-", "message": "File tidak berisi data"}])

    crosses = {k: e >= 1440 for k, (_, e) in rules.items()}
    known = {k: True for k in rules}
    d = df.with_columns(
        nik=pl.col("nik").str.replace(r"\.0+$", ""),
        tanggal_d=pl.col("tanggal").str.slice(0, 10).str.to_date("%Y-%m-%d", strict=False),
        status_c=pl.col("status").str.to_lowercase().replace_strict(STATUS_MAP, default=None, return_dtype=pl.Utf8),
        shift_c=pl.coalesce(pl.col("shift").str.to_uppercase(), pl.lit(DEFAULT_SHIFT)),
        masuk_min=_minutes("jam_masuk"),
        keluar_min=_minutes("jam_keluar"),
    )
    d = d.with_columns(
        status_eff=pl.when(pl.col("status_c").is_not_null())
        .then(pl.col("status_c"))
        .when(pl.col("status").is_null() & pl.col("masuk_min").is_not_null())
        .then(pl.lit("Hadir"))
        .otherwise(None),
        crosses_row=pl.col("shift_c").replace_strict(crosses, default=False, return_dtype=pl.Boolean),
        shift_known=pl.col("shift_c").replace_strict(known, default=False, return_dtype=pl.Boolean),
        dup=~pl.struct(["nik", "tanggal_d"]).is_first_distinct() & pl.col("nik").is_not_null() & pl.col("tanggal_d").is_not_null(),
    )

    def nn(c: str) -> pl.Expr:
        return pl.col(c).is_null()

    hadir = pl.col("status_eff") == "Hadir"
    needs_reason = (pl.col("status_eff") == "Izin") | (pl.col("status_eff") == "Sakit") | (pl.col("status_eff") == "Cuti")
    checks = [
        (nn("nik"), "nik", "Kolom wajib diisi"),
        (nn("nama"), "nama", "Kolom wajib diisi"),
        (nn("tanggal"), "tanggal", "Kolom wajib diisi"),
        (~nn("tanggal") & nn("tanggal_d"), "tanggal", "Format tanggal harus YYYY-MM-DD"),
        (~nn("status") & nn("status_c"), "status", f"Status tidak valid. Gunakan {STATUS_HELP}"),
        (~nn("jam_masuk") & nn("masuk_min"), "jam_masuk", "Format jam harus HH:MM"),
        (~nn("jam_keluar") & nn("keluar_min"), "jam_keluar", "Format jam harus HH:MM"),
        (hadir & nn("jam_masuk"), "jam_masuk", "Jam masuk wajib diisi untuk status Hadir"),
        (hadir & nn("jam_keluar"), "jam_keluar", "Jam keluar wajib diisi untuk status Hadir"),
        (
            ~nn("masuk_min") & ~nn("keluar_min") & (pl.col("keluar_min") < pl.col("masuk_min")) & ~pl.col("crosses_row"),
            "jam_keluar",
            "Jam keluar tidak boleh lebih awal dari jam masuk",
        ),
        (needs_reason & nn("keterangan"), "keterangan", "Keterangan wajib diisi untuk status Izin, Sakit, atau Cuti"),
        (~pl.col("shift_known"), "shift", "Kode shift belum diatur di Pengaturan Jam Kerja"),
        (pl.col("dup"), "nik", "Duplikat nik dan tanggal di dalam file"),
    ]
    errors: list[dict] = []
    total = 0
    for mask, column, message in checks:
        rows = d.filter(mask)["idx"].to_list()
        total += len(rows)
        errors += [{"row": i + 2, "column": column, "message": message} for i in rows[:MAX_ERRORS]]
    errors.sort(key=lambda e: (e["row"], e["column"]))

    result = ValidationResult(
        row_count=d.height,
        error_count=total,
        errors=errors[:MAX_ERRORS],
        preview=df.head(5).select(present).to_dicts(),
    )
    if total == 0:
        result.clean = d.select(
            pl.col("nik"),
            pl.col("nama"),
            pl.col("tanggal_d").alias("tanggal"),
            pl.col("departemen").fill_null("Tanpa divisi"),
            pl.col("shift_c").alias("shift"),
            pl.col("masuk_min").alias("jam_masuk_menit"),
            pl.col("keluar_min").alias("jam_keluar_menit"),
            pl.col("status_c").alias("status_in"),
            pl.col("keterangan"),
            pl.col("lokasi_kerja"),
        )
    return result
