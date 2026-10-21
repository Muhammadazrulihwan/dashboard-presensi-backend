import io
import uuid
from datetime import date, timezone
from pathlib import Path

import xlsxwriter
from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.deps import HR_ONLY, get_current_user
from app.models import (
    ArchiveStatus, ArchivedFile, Attendance, AttendanceCorrection, RawActivity, RawReport, TimeOffRequest,
    Upload, UploadFile as UploadFileRow, UploadStatus, User,
)
from app.schemas import (
    BulkDeleteRequest, BulkDeleteResponse, ConfirmRequest, ConfirmResponse, PackageConfirmRequest,
    PackageConfirmResponse, PackagePreviewResponse, PreviewResponse, SourceFileInfo, TrashRequest,
    UploadFileOut, UploadOut,
)
from app.services import audit
from app.services import archive as archive_svc
from app.services.archive import archive_upload
from app.services.calculator import compute
from app.services.cleanup import discard_file
from app.services.file_reader import read_table
from app.services.importer import (
    clear_sources, find_conflicts, import_rows, save_rekap, save_sources, upsert_employees,
)
from app.services.merge import build_employee_master, merge
from app.services.period import ensure_open
from app.services.rules import holidays_list, rules_frame, rules_map
from app.services.sources import SourceError, parse_source
from app.services.validator import validate

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[HR_ONLY])
ALLOWED = {".csv", ".xlsx"}
SOURCE_KINDS = ("activity", "report", "timeoff", "approval")


@router.get("/template")
def download_template():
    buf = io.BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    ws = wb.add_worksheet("Presensi")
    head = wb.add_format({"bold": True, "bg_color": "#E1EFEC", "border": 1})
    text = wb.add_format({"num_format": "@"})
    cols = ["nik", "nama", "tanggal", "departemen", "shift", "jam_masuk", "jam_keluar", "status", "keterangan", "lokasi_kerja"]
    for i, name in enumerate(cols):
        ws.write(0, i, name, head)
    ws.set_column(0, 0, 14, text)  # nik sebagai teks agar angka nol di depan tidak hilang
    ws.set_column(2, 2, 13, text)
    ws.set_column(5, 6, 11, text)
    ws.set_column(1, 1, 24)
    ws.set_column(3, 4, 14)
    ws.set_column(7, 9, 16)
    samples = [
        ["0012345", "Contoh Karyawan", "2026-10-05", "IT", "", "07:58", "17:05", "Hadir", "", "WFO"],
        ["0012345", "Contoh Karyawan", "2026-10-06", "IT", "", "", "", "Sakit", "Demam", "WFH"],
    ]
    for r, row in enumerate(samples, start=1):
        for c, v in enumerate(row):
            ws.write_string(r, c, v)
    info = wb.add_worksheet("Petunjuk")
    info.set_column(0, 0, 110)
    for i, line in enumerate([
        "Satu baris mewakili satu karyawan pada satu tanggal.",
        "Kolom wajib: nik, nama, tanggal (YYYY-MM-DD), jam_masuk (HH:MM), jam_keluar (HH:MM), status.",
        "Status: Hadir, Izin, Sakit, Cuti, Dinas Luar, Alpa. Jika kosong dan tidak ada jam masuk pada hari kerja, dihitung Alpa.",
        "Jam masuk dan jam keluar wajib untuk status Hadir. Keterangan wajib untuk Izin, Sakit, dan Cuti.",
        "departemen disarankan diisi. shift boleh kosong (dianggap REGULER). Kode shift harus sudah diatur di menu Pengaturan.",
        "Kolom keterlambatan, durasi, dan lembur dihitung otomatis oleh sistem, tidak perlu diisi.",
    ]):
        info.write(i, 0, line)
    wb.close()
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="template_presensi.xlsx"'},
    )


def _save_stream(file: UploadFile, dest: Path) -> int:
    """Tulis file ke disk per potongan 1 MB, berhenti jika melebihi batas ukuran."""
    limit = settings.max_upload_mb * 1024 * 1024
    size = 0
    with open(dest, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"Ukuran file melebihi {settings.max_upload_mb} MB")
            out.write(chunk)
    return size


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/preview", response_model=PreviewResponse)
def preview(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    original = Path(file.filename or "upload").name
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "Format file tidak didukung. Gunakan .csv atau .xlsx")

    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.storage_dir / f"{uuid.uuid4().hex}{ext}"
    _save_stream(file, dest)

    up = Upload(filename=original, stored_path=str(dest), uploaded_by=user.id, status=UploadStatus.pending)
    db.add(up)
    db.commit()

    try:
        df, mapped = read_table(dest)
        result = validate(df, rules_map(db))
    except Exception:
        up.status, up.row_count = UploadStatus.failed, 0
        up.error_summary = {"message": "File tidak dapat dibaca"}
        db.commit()
        discard_file(up.stored_path)
        raise HTTPException(422, "File tidak dapat dibaca. Pastikan file CSV atau XLSX valid dan tidak rusak")

    conflicts, date_from, date_to, employees = 0, None, None, 0
    if result.error_count:
        up.status, up.row_count = UploadStatus.failed, 0
        up.error_summary = {"count": result.error_count, "errors": result.errors[:50]}
    else:
        up.status, up.row_count = UploadStatus.validated, result.row_count
        clean = result.clean
        conflicts = find_conflicts(db, clean).height
        date_from, date_to, employees = clean["tanggal"].min(), clean["tanggal"].max(), clean["nik"].n_unique()
        up.date_from, up.date_to = date_from, date_to
    audit.log(db, user.id, "upload_preview", {"upload_id": up.id, "filename": original, "errors": result.error_count}, _ip(request))
    db.commit()
    if result.error_count:
        discard_file(up.stored_path)  # file gagal tidak akan dipakai lagi

    return PreviewResponse(
        upload_id=up.id,
        filename=original,
        row_count=result.row_count,
        preview_rows=result.preview,
        errors=result.errors,
        error_count=result.error_count,
        can_save=result.error_count == 0,
        conflict_count=conflicts,
        date_from=date_from,
        date_to=date_to,
        employee_count=employees,
        mapped_columns=mapped,
    )


def _out(up: Upload, uploader: str, sumber: list[UploadFileOut] | None = None) -> UploadOut:
    created = up.created_at
    if created.tzinfo is None:  # SQLite menyimpan UTC tanpa zona waktu
        created = created.replace(tzinfo=timezone.utc)
    return UploadOut(
        id=up.id, filename=up.filename, uploader=uploader, created_at=created, row_count=up.row_count,
        status=up.status.value, date_from=up.date_from, date_to=up.date_to, sumber=sumber or [],
    )


def _sumber_map(db: Session, upload_ids: list[int]) -> dict[int, list[UploadFileOut]]:
    """Rincian file per sumber (jenis, baris, rentang, peringatan) untuk riwayat upload (PRD F16)."""
    if not upload_ids:
        return {}
    rows = db.scalars(
        select(UploadFileRow).where(UploadFileRow.upload_id.in_(upload_ids)).order_by(UploadFileRow.jenis_sumber)
    ).all()
    out: dict[int, list[UploadFileOut]] = {}
    for r in rows:
        v = r.validasi or {}
        out.setdefault(r.upload_id, []).append(UploadFileOut(
            jenis_sumber=r.jenis_sumber,
            nama_file=r.nama_file,
            row_count=r.row_count,
            date_from=v.get("date_from"),
            date_to=v.get("date_to"),
            warnings=list(v.get("warnings") or []),
        ))
    return out


@router.post("/{upload_id}/confirm", response_model=ConfirmResponse)
def confirm(
    upload_id: int,
    request: Request,
    body: ConfirmRequest | None = Body(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    up = db.get(Upload, upload_id)
    if up is None:
        raise HTTPException(404, "Upload tidak ditemukan")
    if up.status != UploadStatus.validated:
        raise HTTPException(409, "Upload ini sudah disimpan atau tidak lolos validasi")
    try:
        df, _ = read_table(Path(up.stored_path))
    except Exception:
        raise HTTPException(410, "File sementara sudah tidak tersedia. Unggah ulang file Anda")
    result = validate(df, rules_map(db))
    if result.error_count or result.clean is None:
        raise HTTPException(409, "File tidak lolos validasi")

    computed = compute(result.clean, rules_frame(db), holidays_list(db))
    # Periode tertutup tidak boleh diubah (PRD F13).
    try:
        ensure_open(db, computed["tanggal"].min(), computed["tanggal"].max())
    except ValueError as e:
        raise HTTPException(409, str(e))
    conflicts = find_conflicts(db, computed)
    mode = body.mode if body else None
    if conflicts.height and mode is None:
        raise HTTPException(409, f"Ada {conflicts.height} data bentrok (nik dan tanggal sama). Pilih Timpa atau Lewati")
    try:
        stats = import_rows(db, up.id, computed, mode, conflicts)
        up.status, up.row_count = UploadStatus.saved, stats["inserted"]
        # Arsipkan file asli (PRD F22): disalin ke penyimpanan terpisah dengan checksum SHA-256.
        archive_upload(db, up, "presensi",
                       periode_awal=computed["tanggal"].min(), periode_akhir=computed["tanggal"].max(),
                       employee_ids=set(computed["nik"].unique().to_list()))
        audit.log(db, user.id, "upload_confirm", {"upload_id": up.id, "mode": mode, **stats}, _ip(request))
        db.commit()
    except Exception:
        db.rollback()
        up.status = UploadStatus.failed
        db.commit()
        raise HTTPException(500, "Gagal menyimpan data. Coba ulangi upload")
    return ConfirmResponse(upload=_out(up, user.full_name), **stats)


@router.get("", response_model=list[UploadOut])
def history(
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    visible = [UploadStatus.saved, UploadStatus.failed, UploadStatus.deleted]
    if status:
        if status not in {s.value for s in visible}:
            raise HTTPException(400, "Status tidak valid")
        visible = [UploadStatus(status)]
    rows = db.execute(
        select(Upload, User.full_name)
        .join(User, User.id == Upload.uploaded_by)
        .where(Upload.status.in_(visible))
        .order_by(Upload.id.desc())
        .limit(limit)
        .offset(offset)
    ).all()
    sumber = _sumber_map(db, [up.id for up, _ in rows])
    return [_out(up, name, sumber.get(up.id)) for up, name in rows]


def _delete_upload_rows(db: Session, ups: list[Upload], user_id: int, alasan: str) -> list[int]:
    """Hapus data presensi beberapa upload sekaligus; file asli dijamin masuk tempat sampah arsip.

    PRD F24: file asli tidak dimusnahkan. Bila sudah ada di arsip, arsipnya dipindah ke tempat sampah
    (masih bisa dipulihkan). Bila belum (upload lama sebelum fitur arsip), file disalin ke arsip dulu.
    Tidak commit; pemanggil yang commit dan mencatat audit.

    Data mentah empat sumber dan baris upload_files ikut dibersihkan agar tidak tertinggal sebagai data
    yatim: baris `uploads` hanya ditandai `deleted` (tidak dihapus), sehingga ON DELETE CASCADE tidak berjalan.
    """
    archive_ids: list[int] = []
    for up in ups:
        archived = db.scalars(
            select(ArchivedFile).where(ArchivedFile.upload_id == up.id, ArchivedFile.status == ArchiveStatus.aktif)
        ).all()
        if not archived and Path(up.stored_path).exists():
            archive_upload(db, up, "presensi", periode_awal=up.date_from, periode_akhir=up.date_to)
            db.flush()
            archived = db.scalars(
                select(ArchivedFile).where(ArchivedFile.upload_id == up.id, ArchivedFile.status == ArchiveStatus.aktif)
            ).all()
        for row in archived:
            archive_svc.trash(db, row, user_id, alasan)
            archive_ids.append(row.id)

        # Rekap Absensi dan data mentah milik paket ini dibersihkan agar tidak menjadi data yatim.
        # Baris upload_files sengaja DIPERTAHANKAN sebagai metadata jejak audit (dipakai tab Riwayat "Dihapus").
        db.execute(delete(Attendance).where(Attendance.upload_id == up.id))
        db.execute(delete(RawActivity).where(RawActivity.upload_id == up.id))
        db.execute(delete(RawReport).where(RawReport.upload_id == up.id))
        db.execute(delete(TimeOffRequest).where(TimeOffRequest.upload_id == up.id))
        db.execute(delete(AttendanceCorrection).where(AttendanceCorrection.upload_id == up.id))
        up.status = UploadStatus.deleted
    return archive_ids


@router.delete("/{upload_id}", response_model=UploadOut)
def delete_upload(
    upload_id: int,
    request: Request,
    body: TrashRequest | None = Body(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Hapus satu paket upload dari dashboard. Data presensi dihapus, file asli masuk tempat sampah arsip."""
    up = db.get(Upload, upload_id)
    if up is None or up.status == UploadStatus.deleted:
        raise HTTPException(404, "Upload tidak ditemukan")
    alasan = (body.alasan.strip() if body and body.alasan else "") or "Dihapus dari Riwayat Upload"

    # Periode tertutup tidak boleh diubah (PRD F13).
    if up.date_from and up.date_to:
        try:
            ensure_open(db, up.date_from, up.date_to)
        except ValueError as e:
            raise HTTPException(409, str(e))

    archive_ids = _delete_upload_rows(db, [up], user.id, alasan)
    audit.log(db, user.id, "upload_delete", {
        "upload_id": up.id, "filename": up.filename, "alasan": alasan,
        "arsip_ke_tempat_sampah": archive_ids,
    }, _ip(request))
    db.commit()
    uploader = db.get(User, up.uploaded_by)
    return _out(up, uploader.full_name if uploader else "-")


@router.post("/bulk-delete", response_model=BulkDeleteResponse)
def bulk_delete_uploads(
    request: Request,
    body: BulkDeleteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Hapus beberapa paket upload sekaligus (PRD F24). File asli semua paket masuk tempat sampah arsip."""
    ids = list(dict.fromkeys(body.ids))  # buang duplikat, jaga urutan
    ups = list(db.scalars(select(Upload).where(Upload.id.in_(ids), Upload.status != UploadStatus.deleted)))
    found = {u.id for u in ups}
    tidak_ditemukan = [i for i in ids if i not in found]
    if not ups:
        raise HTTPException(404, "Tidak ada upload yang bisa dihapus")

    # Periode tertutup tidak boleh diubah (PRD F13). Tolak seluruh permintaan bila ada satu saja yang terkunci.
    for u in ups:
        if u.date_from and u.date_to:
            try:
                ensure_open(db, u.date_from, u.date_to)
            except ValueError as e:
                raise HTTPException(409, f"{u.filename}: {e}")

    alasan = (body.alasan or "").strip() or "Dihapus massal dari Riwayat Upload"
    archive_ids = _delete_upload_rows(db, ups, user.id, alasan)
    audit.log(db, user.id, "upload_bulk_delete", {
        "upload_ids": [u.id for u in ups], "jumlah": len(ups), "alasan": alasan,
        "tidak_ditemukan": tidak_ditemukan, "arsip_ke_tempat_sampah": archive_ids,
    }, _ip(request))
    db.commit()
    return BulkDeleteResponse(dihapus=len(ups), tidak_ditemukan=tidak_ditemukan, arsip_ke_tempat_sampah=len(archive_ids))


# ---------- Paket empat sumber (PRD F4-F9, Rilis B) ----------

def _save_upload_file(file: UploadFile, jenis: str) -> tuple[Path, str]:
    original = Path(file.filename or "upload").name
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"File {jenis}: format harus .csv atau .xlsx")
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.storage_dir / f"{uuid.uuid4().hex}{ext}"
    _save_stream(file, dest)
    return dest, original


def _parse_package(files: dict[str, tuple[Path, str]]) -> tuple[dict, list[str]]:
    """Parse semua file paket. Kembalikan (parsed, errors)."""
    parsed: dict = {}
    errors: list[str] = []
    for kind, (path, _name) in files.items():
        try:
            parsed[kind] = parse_source(kind, path)
        except SourceError as e:
            errors.append(f"{kind}: {e}")
    return parsed, errors


def _merge_from_parsed(db: Session, parsed: dict) -> tuple[list[dict], date, date]:
    """Gabungkan sumber menjadi Rekap Absensi. Rentang diambil dari tanggal data terkumpul."""
    acts = parsed["activity"].rows if "activity" in parsed else []
    reps = parsed["report"].rows if "report" in parsed else []
    tios = parsed["timeoff"].rows if "timeoff" in parsed else []
    apps = parsed["approval"].rows if "approval" in parsed else []
    dates = [r["tanggal"] for r in acts] + [r["tanggal"] for r in reps] + [r["tanggal_efektif"] for r in apps]
    for t in tios:
        dates.append(t["tanggal_mulai"])
        if t.get("tanggal_selesai"):
            dates.append(t["tanggal_selesai"])
    if not dates:
        raise HTTPException(422, "Tidak ada tanggal data pada file yang diunggah")
    d1, d2 = min(dates), max(dates)
    rows = merge(
        activity=acts, report=reps, timeoff=tios, approval=apps,
        rules=rules_map(db), holidays=set(holidays_list(db)), tanggal_awal=d1, tanggal_akhir=d2,
    )
    return rows, d1, d2


@router.post("/package/preview", response_model=PackagePreviewResponse)
async def package_preview(
    request: Request,
    activity: UploadFile | None = File(None),
    report: UploadFile | None = File(None),
    timeoff: UploadFile | None = File(None),
    approval: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Pratinjau paket empat sumber. Minimal satu file; yang belum ada boleh diunggah belakangan."""
    incoming = {"activity": activity, "report": report, "timeoff": timeoff, "approval": approval}
    files: dict[str, tuple[Path, str]] = {}
    for kind, f in incoming.items():
        if f is not None:
            files[kind] = _save_upload_file(f, kind)
    if not files:
        raise HTTPException(422, "Unggah minimal satu file sumber")

    # Satu paket Upload menampung semua file; stored_path diisi file pertama sebagai ringkasan.
    first_path, first_name = next(iter(files.values()))
    up = Upload(filename=first_name, stored_path=str(first_path), uploaded_by=user.id, status=UploadStatus.pending)
    db.add(up)
    db.commit()

    parsed, errors = _parse_package(files)
    infos = [
        SourceFileInfo(
            jenis_sumber=kind, nama_file=files[kind][1], row_count=p.row_count,
            warnings=p.warnings, mapped_columns=p.mapped_columns, date_from=p.date_from, date_to=p.date_to,
        )
        for kind, p in parsed.items()
    ]

    # Simpan metadata per file (baris, rentang, peringatan) agar riwayat upload bisa menampilkan
    # rincian per sumber tanpa membuka file lagi (PRD F16).
    for kind, (path, name) in files.items():
        p = parsed.get(kind)
        db.add(UploadFileRow(
            upload_id=up.id, jenis_sumber=kind, nama_file=name, stored_path=str(path),
            row_count=p.row_count if p else None,
            validasi={
                "ok": p is not None,
                "warnings": list(p.warnings) if p else [],
                "mapped_columns": list(p.mapped_columns) if p else [],
                "date_from": p.date_from.isoformat() if p and p.date_from else None,
                "date_to": p.date_to.isoformat() if p and p.date_to else None,
            },
        ))
    db.commit()

    can_save = not errors
    d1 = d2 = None
    employee_count = rekap_rows = conflict_count = 0
    status_counts: dict[str, int] = {}
    if can_save:
        try:
            rows, d1, d2 = _merge_from_parsed(db, parsed)
            rekap_rows = len(rows)
            employee_count = len({r["nik"] for r in rows})
            for r in rows:
                status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
            import polars as pl

            df = pl.DataFrame({"nik": [r["nik"] for r in rows], "tanggal": [r["tanggal"] for r in rows]},
                              schema={"nik": pl.Utf8, "tanggal": pl.Date})
            conflict_count = find_conflicts(db, df).height if rows else 0
        except HTTPException as e:
            errors.append(str(e.detail))
            can_save = False

    up.status = UploadStatus.validated if can_save else UploadStatus.failed
    up.row_count = rekap_rows
    up.date_from, up.date_to = d1, d2
    db.commit()
    audit.log(db, user.id, "package_preview", {
        "upload_id": up.id, "sumber": list(files), "rekap_rows": rekap_rows, "errors": len(errors),
    }, _ip(request))
    db.commit()

    return PackagePreviewResponse(
        upload_id=up.id, files=infos, errors=errors, can_save=can_save,
        date_from=d1, date_to=d2, employee_count=employee_count, rekap_rows=rekap_rows,
        status_counts=status_counts, conflict_count=conflict_count,
    )


@router.post("/package/{upload_id}/confirm", response_model=PackageConfirmResponse)
def package_confirm(
    upload_id: int,
    request: Request,
    body: PackageConfirmRequest | None = Body(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Simpan paket empat sumber: data mentah + Rekap Absensi terpadu + master karyawan."""
    up = db.get(Upload, upload_id)
    if up is None:
        raise HTTPException(404, "Upload tidak ditemukan")
    if up.status != UploadStatus.validated:
        raise HTTPException(409, "Paket ini sudah disimpan atau tidak lolos validasi")

    rows_files = db.scalars(select(UploadFileRow).where(UploadFileRow.upload_id == up.id)).all()
    if not rows_files:
        raise HTTPException(409, "File paket tidak ditemukan")
    files = {r.jenis_sumber: (Path(r.stored_path), r.nama_file) for r in rows_files}

    parsed, errors = _parse_package(files)
    if errors:
        raise HTTPException(409, "File tidak lolos validasi: " + "; ".join(errors))

    try:
        rows, d1, d2 = _merge_from_parsed(db, parsed)
        # Periode tertutup tidak boleh diubah (PRD F13).
        try:
            ensure_open(db, d1, d2)
        except ValueError as e:
            raise HTTPException(409, str(e))
        # Data mentah diganti untuk rentang tanggal yang sama, lalu rekap disimpan.
        clear_sources(db, d1, d2)
        sumber = save_sources(db, up.id, parsed)
        employees = upsert_employees(db, build_employee_master(
            parsed["report"].rows if "report" in parsed else [],
            parsed["activity"].rows if "activity" in parsed else [],
        ))
        mode = body.mode if body else None
        stats = save_rekap(db, up.id, rows, mode)
        up.status, up.row_count = UploadStatus.saved, stats["inserted"]
        archive_upload(db, up, "presensi", periode_awal=d1, periode_akhir=d2,
                       employee_ids={r["nik"] for r in rows})
        audit.log(db, user.id, "package_confirm", {
            "upload_id": up.id, "mode": mode, "sumber": sumber, "employees": employees, **stats,
        }, _ip(request))
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        up.status = UploadStatus.failed
        db.commit()
        raise HTTPException(500, "Gagal menyimpan paket. Coba ulangi upload")

    return PackageConfirmResponse(
        upload=_out(up, user.full_name), inserted=stats["inserted"], overwritten=stats["overwritten"],
        skipped=stats["skipped"], sumber=sumber, employees=employees,
    )

