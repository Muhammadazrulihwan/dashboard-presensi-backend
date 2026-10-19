"""Parser untuk file Kinerja Penjualan (2 sheet: Penjualan dan Target)

Format file sesuai PRD v0.6 bagian 13:
- Sheet Penjualan: employee_id, nama, cabang, periode, penjualan, penerimaan, piutang_outlet, sisa_bad_debt
- Sheet Target: level (Perusahaan/Cabang/Salesman), cabang, employee_id, periode, target_penjualan

Periode format: YYYY-MM (contoh: 2026-09)
"""
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

# Kolom wajib per sheet
REQUIRED_PENJUALAN = ["employee_id", "nama", "cabang", "periode", "penjualan"]
REQUIRED_TARGET = ["level", "periode", "target_penjualan"]
OPTIONAL_PENJUALAN = ["penerimaan", "piutang_outlet", "sisa_bad_debt"]

# Alias kolom untuk fleksibilitas
ALIASES = {
    "nik": "employee_id",
    "nip": "employee_id",
    "id_karyawan": "employee_id",
    "nama_karyawan": "nama",
    "name": "nama",
    "branch": "cabang",
    "periode_bulan": "periode",
    "bulan": "periode",
    "month": "periode",
    "sales": "penjualan",
    "nilai_penjualan": "penjualan",
    "total_penjualan": "penjualan",
    "receipt": "penerimaan",
    "piutang": "piutang_outlet",
    "bad_debt": "sisa_bad_debt",
    "tingkat": "level",
    "jenis": "level",
    "target": "target_penjualan",
    "target_sales": "target_penjualan",
}

VALID_LEVELS = {"Perusahaan", "Cabang", "Salesman"}


@dataclass
class SalesValidationResult:
    row_count_penjualan: int = 0
    row_count_target: int = 0
    error_count: int = 0
    errors: list[dict] = field(default_factory=list)
    clean_penjualan: pl.DataFrame | None = None
    clean_target: pl.DataFrame | None = None
    warnings: list[str] = field(default_factory=list)


def _normalize_column(name: str) -> str:
    """Normalisasi nama kolom: lowercase, strip, ganti spasi/dash dengan underscore"""
    n = name.strip().lower()
    n = n.replace(" ", "_").replace("-", "_").replace("/", "_")
    return n


def _map_columns(df: pl.DataFrame, required: list[str], optional: list[str]) -> tuple[pl.DataFrame, list[str]]:
    """Map kolom menggunakan aliases dan kembalikan kolom yang hilang"""
    mapping = {}
    used = set()
    
    for col in df.columns:
        norm = _normalize_column(col)
        target = ALIASES.get(norm, norm)
        if target not in used:
            mapping[col] = target
            used.add(target)
    
    df = df.rename(mapping)
    missing = [c for c in required if c not in df.columns]
    
    # Tambahkan kolom opsional yang tidak ada sebagai null
    for c in optional:
        if c not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias(c))
    
    return df, missing


def read_sales_file(path: Path) -> SalesValidationResult:
    """Baca dan validasi file Excel Kinerja Penjualan dengan 2 sheet"""
    result = SalesValidationResult()
    
    try:
        # Baca kedua sheet
        df_penjualan = pl.read_excel(path, sheet_name="Penjualan", engine="calamine", infer_schema_length=0)
        df_target = pl.read_excel(path, sheet_name="Target", engine="calamine", infer_schema_length=0)
    except Exception as e:
        result.errors.append({"sheet": "-", "row": 1, "column": "-", "message": f"File tidak dapat dibaca: {str(e)}"})
        result.error_count = 1
        return result
    
    # Validasi sheet Penjualan
    df_penjualan, missing_penjualan = _map_columns(df_penjualan, REQUIRED_PENJUALAN, OPTIONAL_PENJUALAN)
    if missing_penjualan:
        result.errors.append({
            "sheet": "Penjualan",
            "row": 1,
            "column": ", ".join(missing_penjualan),
            "message": "Kolom wajib tidak ditemukan"
        })
        result.error_count += 1
    
    # Validasi sheet Target
    df_target, missing_target = _map_columns(df_target, REQUIRED_TARGET, ["cabang", "employee_id"])
    if missing_target:
        result.errors.append({
            "sheet": "Target",
            "row": 1,
            "column": ", ".join(missing_target),
            "message": "Kolom wajib tidak ditemukan"
        })
        result.error_count += 1
    
    if result.error_count > 0:
        return result
    
    # Cast ke string dan bersihkan
    df_penjualan = df_penjualan.with_columns(pl.all().cast(pl.Utf8))
    df_target = df_target.with_columns(pl.all().cast(pl.Utf8))
    
    # Hapus baris kosong
    df_penjualan = df_penjualan.filter(~pl.col("employee_id").is_null() | ~pl.col("penjualan").is_null())
    df_target = df_target.filter(~pl.col("level").is_null() | ~pl.col("target_penjualan").is_null())
    
    result.row_count_penjualan = df_penjualan.height
    result.row_count_target = df_target.height
    
    if result.row_count_penjualan == 0:
        result.errors.append({"sheet": "Penjualan", "row": 2, "column": "-", "message": "Sheet tidak berisi data"})
        result.error_count += 1
    
    if result.row_count_target == 0:
        result.errors.append({"sheet": "Target", "row": 2, "column": "-", "message": "Sheet tidak berisi data"})
        result.error_count += 1
    
    if result.error_count > 0:
        return result
    
    # Validasi data Penjualan
    errors_penjualan = _validate_penjualan(df_penjualan)
    result.errors.extend(errors_penjualan)
    
    # Validasi data Target
    errors_target = _validate_target(df_target)
    result.errors.extend(errors_target)
    
    result.error_count = len(result.errors)
    
    if result.error_count == 0:
        result.clean_penjualan = _clean_penjualan(df_penjualan)
        result.clean_target = _clean_target(df_target)
        result.warnings = _check_consistency(result.clean_penjualan, result.clean_target)
    
    return result


def _validate_penjualan(df: pl.DataFrame) -> list[dict]:
    """Validasi data sheet Penjualan"""
    errors = []
    max_errors = 50
    
    # Cek employee_id tidak kosong
    null_ids = df.filter(pl.col("employee_id").is_null() | (pl.col("employee_id").str.strip_chars() == ""))
    for idx in null_ids.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Penjualan", "row": idx + 2, "column": "employee_id", "message": "Kolom wajib diisi"})
    
    # Cek periode format YYYY-MM
    invalid_periode = df.filter(~pl.col("periode").str.contains(r"^\d{4}-\d{2}$"))
    for idx in invalid_periode.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Penjualan", "row": idx + 2, "column": "periode", "message": "Format periode harus YYYY-MM"})
    
    # Cek penjualan adalah angka
    df_numeric = df.with_columns(
        penjualan_num=pl.col("penjualan").str.replace_all(",", "").cast(pl.Float64, strict=False)
    )
    invalid_num = df_numeric.filter(pl.col("penjualan_num").is_null())
    for idx in invalid_num.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Penjualan", "row": idx + 2, "column": "penjualan", "message": "Harus berupa angka"})
    
    # Cek duplikat employee_id + periode
    dup = df.filter(~pl.struct(["employee_id", "periode"]).is_first_distinct())
    for idx in dup.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Penjualan", "row": idx + 2, "column": "employee_id", "message": "Duplikat employee_id dan periode"})
    
    return errors[:max_errors]


def _validate_target(df: pl.DataFrame) -> list[dict]:
    """Validasi data sheet Target"""
    errors = []
    max_errors = 50
    
    # Cek level valid
    invalid_level = df.filter(~pl.col("level").is_in(VALID_LEVELS))
    for idx in invalid_level.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Target", "row": idx + 2, "column": "level", "message": f"Level harus salah satu: {', '.join(VALID_LEVELS)}"})
    
    # Cek periode format
    invalid_periode = df.filter(~pl.col("periode").str.contains(r"^\d{4}-\d{2}$"))
    for idx in invalid_periode.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Target", "row": idx + 2, "column": "periode", "message": "Format periode harus YYYY-MM"})
    
    # Cek target adalah angka
    df_numeric = df.with_columns(
        target_num=pl.col("target_penjualan").str.replace_all(",", "").cast(pl.Float64, strict=False)
    )
    invalid_num = df_numeric.filter(pl.col("target_num").is_null())
    for idx in invalid_num.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Target", "row": idx + 2, "column": "target_penjualan", "message": "Harus berupa angka"})
    
    # Validasi logika level
    # Level Perusahaan: tidak boleh ada cabang atau employee_id
    perusahaan = df.filter(pl.col("level") == "Perusahaan")
    invalid = perusahaan.filter(pl.col("cabang").is_not_null() | pl.col("employee_id").is_not_null())
    for idx in invalid.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Target", "row": idx + 2, "column": "level", "message": "Level Perusahaan tidak boleh punya cabang atau employee_id"})
    
    # Level Cabang: wajib ada cabang, tidak boleh ada employee_id
    cabang = df.filter(pl.col("level") == "Cabang")
    invalid = cabang.filter(pl.col("cabang").is_null() | pl.col("employee_id").is_not_null())
    for idx in invalid.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Target", "row": idx + 2, "column": "level", "message": "Level Cabang wajib punya cabang, tidak boleh ada employee_id"})
    
    # Level Salesman: wajib ada employee_id dan cabang
    salesman = df.filter(pl.col("level") == "Salesman")
    invalid = salesman.filter(pl.col("employee_id").is_null())
    for idx in invalid.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Target", "row": idx + 2, "column": "employee_id", "message": "Level Salesman wajib punya employee_id"})
    
    # Cek duplikat (level, cabang, employee_id, periode)
    dup = df.filter(~pl.struct(["level", "cabang", "employee_id", "periode"]).is_first_distinct())
    for idx in dup.select(pl.int_range(0, pl.len())).to_series().to_list()[:max_errors]:
        errors.append({"sheet": "Target", "row": idx + 2, "column": "level", "message": "Duplikat kombinasi level, cabang, employee_id, periode"})
    
    return errors[:max_errors]


def _clean_penjualan(df: pl.DataFrame) -> pl.DataFrame:
    """Bersihkan dan konversi data Penjualan"""
    return df.with_columns([
        pl.col("employee_id").str.strip_chars().str.replace(r"\.0+$", ""),
        pl.col("nama").str.strip_chars(),
        pl.col("cabang").str.strip_chars(),
        pl.col("periode").str.strip_chars(),
        pl.col("penjualan").str.replace_all(",", "").cast(pl.Float64),
        pl.col("penerimaan").str.replace_all(",", "").cast(pl.Float64, strict=False),
        pl.col("piutang_outlet").str.replace_all(",", "").cast(pl.Float64, strict=False),
        pl.col("sisa_bad_debt").str.replace_all(",", "").cast(pl.Float64, strict=False),
    ]).select([
        "employee_id", "nama", "cabang", "periode",
        "penjualan", "penerimaan", "piutang_outlet", "sisa_bad_debt"
    ])


def _clean_target(df: pl.DataFrame) -> pl.DataFrame:
    """Bersihkan dan konversi data Target"""
    return df.with_columns([
        pl.col("level").str.strip_chars(),
        pl.col("cabang").str.strip_chars(),
        pl.col("employee_id").str.strip_chars().str.replace(r"\.0+$", ""),
        pl.col("periode").str.strip_chars(),
        pl.col("target_penjualan").str.replace_all(",", "").cast(pl.Float64),
    ]).select([
        "level", "cabang", "employee_id", "periode", "target_penjualan"
    ])


def _check_consistency(df_penjualan: pl.DataFrame, df_target: pl.DataFrame) -> list[str]:
    """Cek konsistensi antar sheet dan kembalikan warnings"""
    warnings = []
    
    # Cek apakah semua salesman di Penjualan punya target
    salesman_ids = set(df_penjualan["employee_id"].unique().to_list())
    target_ids = set(df_target.filter(pl.col("level") == "Salesman")["employee_id"].unique().to_list())
    missing = salesman_ids - target_ids
    if missing:
        warnings.append(f"{len(missing)} salesman di sheet Penjualan tidak punya target di sheet Target")
    
    # Cek apakah semua cabang di Penjualan punya target cabang
    cabang_penjualan = set(df_penjualan["cabang"].unique().to_list())
    cabang_target = set(df_target.filter(pl.col("level") == "Cabang")["cabang"].unique().to_list())
    missing_cabang = cabang_penjualan - cabang_target
    if missing_cabang:
        warnings.append(f"{len(missing_cabang)} cabang di sheet Penjualan tidak punya target cabang")
    
    # Cek apakah ada target perusahaan
    has_perusahaan = df_target.filter(pl.col("level") == "Perusahaan").height > 0
    if not has_perusahaan:
        warnings.append("Tidak ada target level Perusahaan di sheet Target")

    warnings.extend(_check_target_levels(df_penjualan, df_target))
    return warnings


def _rp(value: float) -> str:
    return "Rp" + f"{int(round(value)):,}".replace(",", ".")


def _check_target_levels(df_penjualan: pl.DataFrame, df_target: pl.DataFrame) -> list[str]:
    """Selisih antar tingkat target (PRD F33). Hanya ditandai untuk ditinjau, tidak menghalangi penyimpanan.

    Jumlah target tingkat bawah tidak harus sama dengan tingkat di atasnya. Pemeriksaan memakai isi file ini saja,
    jadi file untuk sebagian cabang bisa menampilkan selisih terhadap target perusahaan yang wajar.
    """
    warnings: list[str] = []
    rows = list(df_target.iter_rows(named=True))

    pen_cabang = {(r["periode"], r["cabang"]) for r in df_penjualan.iter_rows(named=True)}
    target_cabang_nama = {(r["periode"], r["cabang"]) for r in rows if r["level"] in ("Cabang", "Salesman") and r["cabang"]}

    # Cabang di sheet Target yang tidak ada di sheet Penjualan pada periode yang sama
    tanpa_penjualan = sorted(target_cabang_nama - pen_cabang)
    if tanpa_penjualan:
        shown = ", ".join(f"{c} ({p})" for p, c in tanpa_penjualan[:5])
        extra = f" dan {len(tanpa_penjualan) - 5} lainnya" if len(tanpa_penjualan) > 5 else ""
        warnings.append(f"{len(tanpa_penjualan)} cabang di sheet Target tidak ada di sheet Penjualan pada periode yang sama: {shown}{extra}")

    # Jumlah target salesman per cabang dibandingkan target cabang
    target_cabang: dict[tuple, float] = {}
    jumlah_salesman: dict[tuple, float] = {}
    target_perusahaan: dict[str, float] = {}
    for r in rows:
        key = (r["periode"], r["cabang"])
        if r["level"] == "Cabang":
            target_cabang[key] = r["target_penjualan"]
        elif r["level"] == "Salesman" and r["cabang"]:
            jumlah_salesman[key] = jumlah_salesman.get(key, 0.0) + r["target_penjualan"]
        elif r["level"] == "Perusahaan":
            target_perusahaan[r["periode"]] = r["target_penjualan"]

    beda = [
        f"{p} {c}: salesman {_rp(jumlah_salesman[(p, c)])}, cabang {_rp(t)}"
        for (p, c), t in sorted(target_cabang.items())
        if (p, c) in jumlah_salesman and abs(jumlah_salesman[(p, c)] - t) >= 0.5
    ]
    if beda:
        warnings.append("Jumlah target salesman tidak sama dengan target cabang, mohon ditinjau: " + "; ".join(beda[:5])
                        + (f"; dan {len(beda) - 5} lainnya" if len(beda) > 5 else ""))

    # Jumlah target cabang dibandingkan target perusahaan
    jumlah_cabang: dict[str, float] = {}
    for (p, _c), t in target_cabang.items():
        jumlah_cabang[p] = jumlah_cabang.get(p, 0.0) + t
    beda = [
        f"{p}: cabang {_rp(jumlah_cabang[p])}, perusahaan {_rp(t)}"
        for p, t in sorted(target_perusahaan.items())
        if p in jumlah_cabang and abs(jumlah_cabang[p] - t) >= 0.5
    ]
    if beda:
        warnings.append("Jumlah target cabang di file ini tidak sama dengan target perusahaan, mohon ditinjau: " + "; ".join(beda))

    return warnings
