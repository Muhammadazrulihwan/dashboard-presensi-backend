"""Router untuk Kinerja Penjualan (Rilis E)

Upload file Excel 2 sheet (Penjualan + Target) oleh Sales/Marketing.
Dashboard untuk HR, Audit, Sales/Marketing.
Persetujuan Penilaian oleh akun Sales/Marketing yang ditandai penyetuju.
"""
import calendar
import uuid
from datetime import date, timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import get_db
from app.deps import PERFORMANCE, SALES_ONLY, get_current_user
from app.models import (
    AssessmentStatus, Attendance, EligibilityRecommendation, SalesAssessment, SalesPerformance,
    SalesTarget, TargetLevel, Upload, UploadStatus, User
)
from app.schemas import (
    AssessmentUpdate, CabangPerformance, PerusahaanPerformance, SalesConfirmResponse,
    SalesmanPerformance, SalesPreviewResponse, SalesReuploadInfo, SalesUploadOut
)
from app.services import audit
from app.services.aggregator import _counters
from app.services.archive import archive_upload
from app.services import period as period_svc
from app.services.sales_parser import read_sales_file
from app.services.sales_template import create_sales_template

router = APIRouter(prefix="/sales", tags=["sales"])

ALLOWED_EXT = {".xlsx"}


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/template", dependencies=[SALES_ONLY])
def download_sales_template():
    """Download template Excel untuk upload Kinerja Penjualan (2 sheet: Penjualan + Target)"""
    buf = create_sales_template()
    return Response(
        buf.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="Template_Kinerja_Penjualan.xlsx"'}
    )


@router.get("/meta", dependencies=[PERFORMANCE])
def get_sales_meta(db: Session = Depends(get_db)):
    """Daftar periode (terbaru dulu) dan cabang yang sudah punya data, untuk pilihan filter di dashboard."""
    periodes = db.scalars(select(SalesPerformance.periode).distinct().order_by(SalesPerformance.periode.desc())).all()
    cabang = db.scalars(select(SalesPerformance.cabang).distinct().order_by(SalesPerformance.cabang)).all()
    return {"periodes": list(periodes), "cabang": list(cabang)}


@router.post("/preview", response_model=SalesPreviewResponse, dependencies=[SALES_ONLY])
def preview_sales_upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Preview dan validasi file Kinerja Penjualan (2 sheet: Penjualan + Target) - Sales/Marketing only"""
    original = Path(file.filename or "upload").name
    ext = Path(original).suffix.lower()
    
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Format file tidak didukung. Gunakan .xlsx")
    
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.storage_dir / f"{uuid.uuid4().hex}{ext}"
    
    # Simpan file sementara
    with open(dest, "wb") as out:
        while chunk := file.file.read(1024 * 1024):
            out.write(chunk)
    
    # Buat record upload
    up = Upload(
        filename=original,
        stored_path=str(dest),
        uploaded_by=user.id,
        status=UploadStatus.pending
    )
    db.add(up)
    db.commit()
    
    # Parse dan validasi
    try:
        result = read_sales_file(dest)
    except Exception as e:
        up.status = UploadStatus.failed
        up.error_summary = {"message": f"File tidak dapat dibaca: {str(e)}"}
        db.commit()
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"File tidak dapat dibaca: {str(e)}")
    
    if result.error_count > 0:
        up.status = UploadStatus.failed
        up.row_count = 0
        up.error_summary = {"errors": result.errors[:50]}
        db.commit()
        dest.unlink(missing_ok=True)
    else:
        up.status = UploadStatus.validated
        up.row_count = result.row_count_penjualan + result.row_count_target
        db.commit()
    
    audit.log(db, user.id, "sales_preview", {
        "upload_id": up.id,
        "filename": original,
        "penjualan": result.row_count_penjualan,
        "target": result.row_count_target,
        "errors": result.error_count
    }, _ip(request))
    db.commit()
    
    reupload: list[SalesReuploadInfo] = []
    if result.error_count == 0:
        result.warnings.extend(_presensi_warnings(db, result.clean_penjualan))
        scope = _replace_scope(db, result.clean_penjualan, result.clean_target)
        for periode, s in sorted(scope.items()):
            if s["old_ids"] or s["decided"]:
                reupload.append(SalesReuploadInfo(
                    periode=periode,
                    cabang=sorted(s["cabang"]),
                    penjualan_diganti=len(s["old_ids"]),
                    penilaian_diputuskan=s["decided"],
                ))

    return SalesPreviewResponse(
        upload_id=up.id,
        filename=original,
        row_count_penjualan=result.row_count_penjualan,
        row_count_target=result.row_count_target,
        errors=result.errors,
        error_count=result.error_count,
        warnings=result.warnings,
        can_save=result.error_count == 0,
        reupload=reupload,
        perlu_konfirmasi=any(r.penilaian_diputuskan > 0 for r in reupload),
    )


@router.post("/{upload_id}/confirm", response_model=SalesConfirmResponse, dependencies=[SALES_ONLY])
def confirm_sales_upload(
    upload_id: int,
    request: Request,
    timpa: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Simpan data Kinerja Penjualan dan buat rekomendasi kelayakan otomatis - Sales/Marketing only

    Unggah ulang hanya mengganti cabang yang ada di file. Bila ada penilaian yang sudah Disetujui atau Ditolak
    pada data yang diganti, konfirmasi (timpa=true) wajib dan statusnya kembali ke Draf (PRD bagian 13).
    """
    up = db.get(Upload, upload_id)
    if up is None:
        raise HTTPException(404, "Upload tidak ditemukan")
    if up.status != UploadStatus.validated:
        raise HTTPException(409, "Upload ini sudah disimpan atau tidak lolos validasi")
    # Kunci baris upload agar dua permintaan konfirmasi bersamaan tidak memproses data dua kali.
    up = db.scalar(select(Upload).where(Upload.id == upload_id).with_for_update())
    if up is None or up.status != UploadStatus.validated:
        raise HTTPException(409, "Upload ini sudah disimpan atau tidak lolos validasi")
    
    # Parse ulang file
    try:
        result = read_sales_file(Path(up.stored_path))
    except Exception:
        raise HTTPException(410, "File sementara sudah tidak tersedia. Unggah ulang file Anda")
    
    if result.error_count > 0 or result.clean_penjualan is None or result.clean_target is None:
        raise HTTPException(409, "File tidak lolos validasi")
    
    # Unggah ulang yang menimpa penilaian yang sudah diputuskan wajib dikonfirmasi
    scope = _replace_scope(db, result.clean_penjualan, result.clean_target)
    diputuskan = sum(s["decided"] for s in scope.values())
    if diputuskan > 0 and not timpa:
        raise HTTPException(
            409,
            f"{diputuskan} penilaian sudah Disetujui atau Ditolak dan akan kembali ke Draf. Konfirmasi unggah ulang diperlukan.",
        )

    # Periode tertutup tidak boleh diubah (PRD F13).
    for periode in sorted(scope):
        try:
            tahun, bulan = (int(x) for x in periode.split("-"))
        except ValueError:
            continue
        if period_svc.is_closed(db, tahun, bulan):
            raise HTTPException(
                409,
                f"Periode {periode} sudah ditutup dan tidak dapat diubah. "
                "Buka kembali periode di menu Tutup Periode bila memang perlu diubah.",
            )

    # Simpan data
    try:
        stats = _import_sales_data(db, up.id, result.clean_penjualan, result.clean_target, scope)
        audit_detail = stats.pop("audit")
        up.status = UploadStatus.saved
        up.row_count = stats["inserted_performance"] + stats["inserted_targets"]
        # Arsipkan file asli penjualan (PRD F22). Rentang tanggal data = awal dan akhir bulan periode.
        periods = sorted(set(result.clean_penjualan["periode"].unique().to_list()))
        awal = akhir = None
        if periods:
            y0, m0 = map(int, periods[0].split("-"))
            y1, m1 = map(int, periods[-1].split("-"))
            awal, akhir = date(y0, m0, 1), date(y1, m1, calendar.monthrange(y1, m1)[1])
        archive_upload(db, up, "penjualan", periode_awal=awal, periode_akhir=akhir,
                       employee_ids=set(result.clean_penjualan["employee_id"].unique().to_list()))

        audit.log(db, user.id, "sales_confirm", {
            "upload_id": up.id,
            "unggah_ulang": bool(audit_detail["cabang_diganti"]),
            **stats,
            **audit_detail,
        }, _ip(request))
        db.commit()
    except Exception as e:
        db.rollback()
        up.status = UploadStatus.failed
        db.commit()
        raise HTTPException(500, f"Gagal menyimpan data: {str(e)}")
    
    return SalesConfirmResponse(
        upload=SalesUploadOut(
            id=up.id,
            filename=up.filename,
            uploader=user.full_name,
            created_at=up.created_at,
            row_count=up.row_count,
            status=up.status.value
        ),
        **stats
    )


def _perf_scope(periode: str, s: dict):
    """Baris penjualan yang digantikan file: cabang yang ada di file, atau salesman yang ada di file (pindah cabang)."""
    return and_(
        SalesPerformance.periode == periode,
        or_(SalesPerformance.cabang.in_(list(s["cabang"])), SalesPerformance.employee_id.in_(list(s["new_ids"]))),
    )


def _replace_scope(db: Session, df_penjualan, df_target) -> dict[str, dict]:
    """Cakupan data yang digantikan file ini, per periode.

    Hanya cabang yang ada di file yang diganti; cabang lain pada periode yang sama tidak disentuh.
    """
    scope: dict[str, dict] = {}

    def get(periode: str) -> dict:
        return scope.setdefault(periode, {
            "cabang": set(), "cabang_target": set(), "new_ids": set(), "target_ids": set(), "perusahaan": False,
        })

    for r in df_penjualan.iter_rows(named=True):
        s = get(r["periode"])
        s["cabang"].add(r["cabang"])
        s["cabang_target"].add(r["cabang"])
        s["new_ids"].add(r["employee_id"])
    for r in df_target.iter_rows(named=True):
        s = get(r["periode"])
        if r["level"] == TargetLevel.PERUSAHAAN.value:
            s["perusahaan"] = True
            continue
        if r["cabang"]:
            s["cabang_target"].add(r["cabang"])
        if r["employee_id"]:
            s["target_ids"].add(r["employee_id"])

    for periode, s in scope.items():
        s["old_ids"] = set(db.scalars(select(SalesPerformance.employee_id).where(_perf_scope(periode, s))))
        affected = s["old_ids"] | s["new_ids"]
        s["decided"] = db.scalar(
            select(func.count()).select_from(SalesAssessment).where(
                SalesAssessment.periode == periode,
                SalesAssessment.employee_id.in_(list(affected)),
                SalesAssessment.status != AssessmentStatus.DRAFT,
            )
        ) or 0
    return scope


def _import_sales_data(db: Session, upload_id: int, df_penjualan, df_target, scope: dict[str, dict]):
    """Import data penjualan, target, dan buat assessment otomatis.

    Unggah ulang mengganti hanya cabang di file. Penilaian yang sudah diputuskan pada data yang diganti kembali
    ke Draf (rekomendasi dihitung ulang); penilaian salesman yang tidak ada lagi di file dihapus.
    """
    reset_log: list[dict] = []
    removed = 0

    for periode, s in scope.items():
        # Hapus data lama dalam cakupan file. Pakai DELETE langsung agar tidak bentrok dengan INSERT di bawah.
        db.execute(delete(SalesPerformance).where(_perf_scope(periode, s)))

        cabang_target = list(s["cabang_target"])
        db.execute(delete(SalesTarget).where(
            SalesTarget.periode == periode, SalesTarget.level == TargetLevel.CABANG,
            SalesTarget.cabang.in_(cabang_target),
        ))
        db.execute(delete(SalesTarget).where(
            SalesTarget.periode == periode, SalesTarget.level == TargetLevel.SALESMAN,
            or_(SalesTarget.cabang.in_(cabang_target), SalesTarget.employee_id.in_(list(s["new_ids"] | s["target_ids"]))),
        ))
        if s["perusahaan"]:
            db.execute(delete(SalesTarget).where(
                SalesTarget.periode == periode, SalesTarget.level == TargetLevel.PERUSAHAAN,
            ))

        # Penilaian salesman yang tidak ada lagi di file: dihapus
        gone = list(s["old_ids"] - s["new_ids"])
        if gone:
            for a in db.scalars(select(SalesAssessment).where(
                SalesAssessment.periode == periode, SalesAssessment.employee_id.in_(gone),
            )):
                if a.status != AssessmentStatus.DRAFT:
                    reset_log.append({"employee_id": a.employee_id, "periode": periode, "status_lama": a.status.value,
                                      "catatan_lama": a.notes, "dihapus": True})
                db.delete(a)
                removed += 1

        # Penilaian yang sudah diputuskan: kembali ke Draf, rekomendasi dihitung ulang di _create_assessments
        for a in db.scalars(select(SalesAssessment).where(
            SalesAssessment.periode == periode,
            SalesAssessment.employee_id.in_(list(s["new_ids"])),
            SalesAssessment.status != AssessmentStatus.DRAFT,
        )):
            reset_log.append({"employee_id": a.employee_id, "periode": periode, "status_lama": a.status.value,
                              "catatan_lama": a.notes, "dihapus": False})
            a.status = AssessmentStatus.DRAFT
            a.approved_by = None
            a.approved_at = None
            a.notes = None

    db.flush()

    # Insert performance
    perf_count = 0
    for row in df_penjualan.iter_rows(named=True):
        db.add(SalesPerformance(
            upload_id=upload_id,
            employee_id=row["employee_id"],
            nama=row["nama"],
            cabang=row["cabang"],
            periode=row["periode"],
            penjualan=row["penjualan"],
            penerimaan=row["penerimaan"],
            piutang_outlet=row["piutang_outlet"],
            sisa_bad_debt=row["sisa_bad_debt"],
        ))
        perf_count += 1

    # Insert targets
    target_count = 0
    for row in df_target.iter_rows(named=True):
        db.add(SalesTarget(
            upload_id=upload_id,
            level=TargetLevel(row["level"]),
            cabang=row["cabang"] if row["cabang"] else None,
            employee_id=row["employee_id"] if row["employee_id"] else None,
            periode=row["periode"],
            target=row["target_penjualan"],
        ))
        target_count += 1

    db.flush()

    # Buat atau update assessment dengan rekomendasi otomatis (F34)
    periodes = set(df_penjualan["periode"].unique().to_list())
    assessment_count = _create_assessments(db, periodes)

    return {
        "inserted_performance": perf_count,
        "inserted_targets": target_count,
        "assessments_created": assessment_count,
        "assessments_reset": sum(1 for r in reset_log if not r["dihapus"]),
        "assessments_removed": removed,
        "audit": {
            "cabang_diganti": {p: sorted(s["cabang"]) for p, s in scope.items() if s["old_ids"]},
            "penilaian_direset": reset_log[:100],
        },
    }


def _create_assessments(db: Session, periodes: set[str]) -> int:
    """Buat rekomendasi kelayakan untuk semua salesman pada periode tertentu (PRD F34)
    
    Aturan: Layak bila penjualan >= target pribadi.
    Kekurangan satu salesman tidak ditutup salesman lain.
    """
    count = 0
    
    for periode in periodes:
        # Ambil semua salesman dengan penjualan
        perfs = db.scalars(
            select(SalesPerformance).where(SalesPerformance.periode == periode)
        ).all()
        
        for perf in perfs:
            # Cari target salesman ini
            target_row = db.scalar(
                select(SalesTarget).where(
                    SalesTarget.level == TargetLevel.SALESMAN,
                    SalesTarget.employee_id == perf.employee_id,
                    SalesTarget.periode == periode
                )
            )
            
            if target_row is None:
                recommendation = EligibilityRecommendation.NO_TARGET
            elif perf.penjualan >= target_row.target:
                recommendation = EligibilityRecommendation.ELIGIBLE
            else:
                recommendation = EligibilityRecommendation.NOT_ELIGIBLE
            
            # Cek apakah sudah ada assessment
            existing = db.scalar(
                select(SalesAssessment).where(
                    SalesAssessment.employee_id == perf.employee_id,
                    SalesAssessment.periode == periode
                )
            )
            
            if existing:
                # Update rekomendasi, tapi jangan ubah status yang sudah Disetujui/Ditolak
                if existing.status == AssessmentStatus.DRAFT:
                    existing.recommendation = recommendation
            else:
                # Buat baru dengan status Draf
                assessment = SalesAssessment(
                    employee_id=perf.employee_id,
                    periode=periode,
                    recommendation=recommendation,
                    status=AssessmentStatus.DRAFT
                )
                db.add(assessment)
                count += 1
    
    return count


def _kehadiran_map(db: Session, periode: str, ids: set[str]) -> dict[str, tuple[int, int, int]]:
    """Hari kerja, hari hadir, dan hari terlambat per NIK pada periode itu, dari data presensi (PRD F31).

    Memakai definisi yang sama dengan dashboard presensi. employee_id di file penjualan diasumsikan sama dengan NIK presensi.
    """
    if not ids:
        return {}
    c = _counters()
    rows = db.execute(
        select(Attendance.nik, c["hari_kerja"].label("hk"), c["hadir"].label("hadir"), c["terlambat"].label("late"))
        .where(Attendance.periode_ym == periode, Attendance.nik.in_(list(ids)))
        .group_by(Attendance.nik)
    ).all()
    return {r.nik: (int(r.hk), int(r.hadir), int(r.late)) for r in rows}


def _kehadiran_fields(row: tuple[int, int, int] | None) -> dict:
    """Salesman tanpa data presensi ditampilkan kosong, bukan nol."""
    if row is None or row[0] == 0:
        return {}
    hk, hadir, late = row
    return {"hari_kerja": hk, "hari_hadir": hadir, "kehadiran_pct": round(hadir * 100 / hk, 1), "terlambat_hari": late}


def _presensi_warnings(db: Session, df_penjualan) -> list[str]:
    """Peringatan bila salesman di file penjualan tidak ada di data presensi periode yang sama."""
    ids_by_periode: dict[str, set[str]] = {}
    for r in df_penjualan.iter_rows(named=True):
        ids_by_periode.setdefault(r["periode"], set()).add(r["employee_id"])
    out: list[str] = []
    for periode, ids in sorted(ids_by_periode.items()):
        if not db.scalar(select(func.count()).select_from(Attendance).where(Attendance.periode_ym == periode)):
            out.append(f"Data presensi periode {periode} belum ada, kehadiran dan keterlambatan salesman belum bisa dihitung")
            continue
        found = set(db.scalars(select(Attendance.nik).where(Attendance.periode_ym == periode, Attendance.nik.in_(list(ids))).distinct()))
        missing = sorted(ids - found)
        if missing:
            shown = ", ".join(missing[:5]) + (f" dan {len(missing) - 5} lainnya" if len(missing) > 5 else "")
            out.append(f"{len(missing)} salesman periode {periode} tidak ditemukan di data presensi ({shown})")

        # NIK ada di presensi tetapi tercatat di departemen lain: kemungkinan employee_id bukan NIK karyawan yang sama.
        # Untuk NIK yang tidak ada di presensi periode itu, pakai master karyawan sebagai cadangan.
        beda: dict[str, set[str]] = {}
        for nik, dept in db.execute(
            select(Attendance.nik, Attendance.departemen)
            .where(Attendance.periode_ym == periode, Attendance.nik.in_(list(found))).distinct()
        ):
            if (dept or "").strip().lower() not in settings.sales_orgs:
                beda.setdefault(nik, set()).add(dept or "-")
        if missing:
            from app.models import Employee

            for nik, org in db.execute(
                select(Employee.employee_id, Employee.organisasi).where(Employee.employee_id.in_(missing))
            ):
                if org and org.strip().lower() not in settings.sales_orgs:
                    beda.setdefault(nik, set()).add(f"{org} (master karyawan)")
        if beda:
            items = [f"{nik} ({', '.join(sorted(d))})" for nik, d in sorted(beda.items())]
            shown = ", ".join(items[:5]) + (f" dan {len(items) - 5} lainnya" if len(items) > 5 else "")
            out.append(
                f"{len(beda)} salesman periode {periode} tercatat di departemen presensi selain Sales/Marketing, "
                f"periksa apakah employee_id sama dengan NIK karyawan yang dimaksud ({shown})"
            )
    return out


@router.get("/salesman", response_model=list[SalesmanPerformance], dependencies=[PERFORMANCE])
def get_salesman_performance(
    periode: str,
    cabang: str | None = None,
    db: Session = Depends(get_db)
):
    """Dashboard per salesman dengan kelayakan dan status persetujuan (PRD F31)"""
    query = select(SalesPerformance).where(SalesPerformance.periode == periode)
    if cabang:
        query = query.where(SalesPerformance.cabang == cabang)
    
    perfs = db.scalars(query).all()
    kehadiran = _kehadiran_map(db, periode, {p.employee_id for p in perfs})

    result = []
    for perf in perfs:
        # Ambil target salesman
        target_row = db.scalar(
            select(SalesTarget).where(
                SalesTarget.level == TargetLevel.SALESMAN,
                SalesTarget.employee_id == perf.employee_id,
                SalesTarget.periode == periode
            )
        )
        
        target_value = target_row.target if target_row else None
        capaian_pct = (perf.penjualan / target_value * 100) if target_value and target_value > 0 else None
        
        # Hitung kontribusi ke cabang
        total_cabang = db.scalar(
            select(func.sum(SalesPerformance.penjualan)).where(
                SalesPerformance.cabang == perf.cabang,
                SalesPerformance.periode == periode
            )
        ) or 0
        
        kontribusi_pct = (perf.penjualan / total_cabang * 100) if total_cabang > 0 else None
        
        # Peringkat dalam cabang
        rank = db.scalar(
            select(func.count(SalesPerformance.id) + 1).where(
                SalesPerformance.cabang == perf.cabang,
                SalesPerformance.periode == periode,
                SalesPerformance.penjualan > perf.penjualan
            )
        ) or 1
        
        # Ambil assessment
        assessment = db.scalar(
            select(SalesAssessment).where(
                SalesAssessment.employee_id == perf.employee_id,
                SalesAssessment.periode == periode
            )
        )
        
        if assessment:
            status_kelayakan = assessment.recommendation.value
            assessment_status = assessment.status.value
            approved_by_user = db.get(User, assessment.approved_by) if assessment.approved_by else None
            approved_by = approved_by_user.full_name if approved_by_user else None
            approved_at = assessment.approved_at
            catatan = assessment.notes
        else:
            status_kelayakan = "Target belum ada"
            assessment_status = "Draf"
            approved_by = None
            approved_at = None
            catatan = None
        
        result.append(SalesmanPerformance(
            employee_id=perf.employee_id,
            nama=perf.nama,
            cabang=perf.cabang,
            periode=perf.periode,
            penjualan=perf.penjualan,
            penerimaan=perf.penerimaan,
            piutang_outlet=perf.piutang_outlet,
            sisa_bad_debt=perf.sisa_bad_debt,
            target=target_value,
            capaian_pct=capaian_pct,
            kontribusi_cabang_pct=kontribusi_pct,
            peringkat_cabang=rank,
            status_kelayakan=status_kelayakan,
            assessment_status=assessment_status,
            approved_by=approved_by,
            approved_at=approved_at,
            catatan=catatan,
            **_kehadiran_fields(kehadiran.get(perf.employee_id)),
        ))
    
    # Sort by penjualan descending
    result.sort(key=lambda x: x.penjualan, reverse=True)
    
    return result


# ---------- Agregasi periode: bulan, kuartal, tahun (PRD F31/F35) ----------

def _periods_for(periode: str, granularitas: str) -> list[str]:
    """Daftar bulan "YYYY-MM" yang dicakup satu periode sesuai granularitas.

    - bulan: satu bulan, mis. "2026-09"
    - kuartal: "2026-Q3" -> Juli, Agustus, September
    - tahun: "2026" -> Januari sampai Desember
    """
    g = (granularitas or "bulan").lower()
    if g == "tahun":
        tahun = int(periode)
        return [f"{tahun:04d}-{b:02d}" for b in range(1, 13)]
    if g == "kuartal":
        tahun_s, q_s = periode.upper().replace("Q", "").split("-")
        tahun, kuartal = int(tahun_s), int(q_s)
        awal = (kuartal - 1) * 3 + 1
        return [f"{tahun:04d}-{b:02d}" for b in range(awal, awal + 3)]
    return [periode]


def _validate_period(periode: str, granularitas: str) -> None:
    g = (granularitas or "bulan").lower()
    if g == "tahun":
        if not (len(periode) == 4 and periode.isdigit()):
            raise HTTPException(422, "Periode tahun harus format YYYY, contoh 2026")
    elif g == "kuartal":
        p = periode.upper()
        if not (len(p) == 7 and p[4] == "-" and p[5] == "Q" and p[6] in "1234" and p[:4].isdigit()):
            raise HTTPException(422, "Periode kuartal harus format YYYY-Q1 sampai YYYY-Q4, contoh 2026-Q3")
    else:
        if not (len(periode) == 7 and periode[4] == "-"):
            raise HTTPException(422, "Periode bulan harus format YYYY-MM, contoh 2026-09")


def _remaining_workdays(months: list[str]) -> int | None:
    """Sisa hari kerja (Senin-Sabtu) dari hari ini sampai akhir periode, atau None bila periode sudah lewat.

    Dipakai untuk kebutuhan penjualan per hari kerja (PRD F35). Hari libur tidak dikurangkan karena
    endpoint ini tidak memuat daftar hari libur; angka bersifat indikatif untuk pantauan.
    """
    today = date.today()
    akhir_tahun, akhir_bulan = int(months[-1][:4]), int(months[-1][5:7])
    akhir = date(akhir_tahun, akhir_bulan, calendar.monthrange(akhir_tahun, akhir_bulan)[1])
    if today > akhir:
        return None
    n, d = 0, today
    while d <= akhir:
        if d.isoweekday() <= 6:  # Senin sampai Sabtu
            n += 1
        d += timedelta(days=1)
    return n


@router.get("/cabang", response_model=list[CabangPerformance], dependencies=[PERFORMANCE])
def get_cabang_performance(
    periode: str,
    granularitas: Literal["bulan", "kuartal", "tahun"] = "bulan",
    db: Session = Depends(get_db),
):
    """Dashboard per cabang dengan pantauan capaian (PRD F35).

    Mendukung agregasi bulan, kuartal, dan tahun. Penjualan dan target dijumlahkan dari bulan-bulan
    yang tercakup; capaian = total penjualan / total target pada periode itu.
    """
    _validate_period(periode, granularitas)
    months = _periods_for(periode, granularitas)

    cabangs = db.scalars(
        select(SalesPerformance.cabang).where(SalesPerformance.periode.in_(months)).distinct()
    ).all()

    # Agregasi penjualan per cabang dalam satu query (bukan per cabang) agar tidak N+1.
    penjualan_map = {
        c: float(t or 0)
        for c, t in db.execute(
            select(SalesPerformance.cabang, func.sum(SalesPerformance.penjualan))
            .where(SalesPerformance.periode.in_(months)).group_by(SalesPerformance.cabang)
        ).all()
    }
    jumlah_map = {
        c: int(n or 0)
        for c, n in db.execute(
            select(SalesPerformance.cabang, func.count(SalesPerformance.id))
            .where(SalesPerformance.periode.in_(months)).group_by(SalesPerformance.cabang)
        ).all()
    }
    target_map = {
        c: float(t or 0)
        for c, t in db.execute(
            select(SalesTarget.cabang, func.sum(SalesTarget.target))
            .where(
                SalesTarget.level == TargetLevel.CABANG,
                SalesTarget.periode.in_(months),
                SalesTarget.cabang.is_not(None),
            ).group_by(SalesTarget.cabang)
        ).all()
    }

    # Rekomendasi kelayakan per salesman pada bulan terakhir periode (status terkini).
    bulan_akhir = months[-1]
    rec_map = {
        e: r
        for e, r in db.execute(
            select(SalesAssessment.employee_id, SalesAssessment.recommendation)
            .where(SalesAssessment.periode == bulan_akhir)
        ).all()
    }
    emp_map: dict[str, list[str]] = {}
    for cabang, eid, nama in db.execute(
        select(SalesPerformance.cabang, SalesPerformance.employee_id, SalesPerformance.nama)
        .where(SalesPerformance.periode == bulan_akhir)
        .order_by(SalesPerformance.nama)
    ).all():
        emp_map.setdefault(cabang, []).append(eid)

    sisa_hari = _remaining_workdays(months)
    result = []
    for cabang in cabangs:
        total_penjualan = penjualan_map.get(cabang, 0.0)
        target_value = target_map.get(cabang)
        capaian_pct = (total_penjualan / target_value * 100) if target_value and target_value > 0 else None
        sisa_target = max(0.0, (target_value or 0) - total_penjualan)

        if capaian_pct is None:
            status = "Target belum ada"
        elif capaian_pct >= 100:
            status = "Tercapai"
        elif capaian_pct >= 80:
            status = "Perlu dikejar"
        else:
            status = "Jauh dari target"

        ids = emp_map.get(cabang, [])
        belum = [eid for eid in ids if rec_map.get(eid) == EligibilityRecommendation.NOT_ELIGIBLE]
        # Nama salesman belum layak (PRD F35).
        nama_map = {
            eid: nama
            for eid, nama in db.execute(
                select(SalesPerformance.employee_id, SalesPerformance.nama)
                .where(SalesPerformance.periode == bulan_akhir, SalesPerformance.employee_id.in_(belum or ["-"]))
            ).all()
        }
        belum_nama = [nama_map[e] for e in belum if e in nama_map]

        result.append(CabangPerformance(
            cabang=cabang,
            periode=periode,
            granularitas=granularitas,
            total_penjualan=total_penjualan,
            target_cabang=target_value,
            capaian_pct=capaian_pct,
            sisa_target=sisa_target,
            jumlah_salesman=jumlah_map.get(cabang, 0),
            salesman_layak=sum(1 for e in ids if rec_map.get(e) == EligibilityRecommendation.ELIGIBLE),
            salesman_tidak_layak=len(belum),
            peringkat=None,
            status=status,
            salesman_belum_layak=belum_nama,
            sisa_hari_kerja=sisa_hari,
            kebutuhan_per_hari_kerja=(round(sisa_target / sisa_hari, 2) if sisa_hari and sisa_target > 0 else None),
        ))

    # Peringkat antar cabang berdasar capaian terhadap target sendiri (PRD F35), bukan volume penjualan.
    result.sort(key=lambda x: (x.capaian_pct is not None, x.capaian_pct or 0, x.total_penjualan), reverse=True)
    for idx, item in enumerate(result, start=1):
        item.peringkat = idx
    return result


@router.get("/perusahaan", response_model=PerusahaanPerformance, dependencies=[PERFORMANCE])
def get_perusahaan_performance(
    periode: str,
    granularitas: Literal["bulan", "kuartal", "tahun"] = "bulan",
    db: Session = Depends(get_db),
):
    """Dashboard perusahaan total (PRD F31). Mendukung agregasi bulan, kuartal, dan tahun."""
    _validate_period(periode, granularitas)
    months = _periods_for(periode, granularitas)

    total_penjualan = db.scalar(
        select(func.sum(SalesPerformance.penjualan)).where(SalesPerformance.periode.in_(months))
    ) or 0

    target_value = db.scalar(
        select(func.sum(SalesTarget.target)).where(
            SalesTarget.level == TargetLevel.PERUSAHAAN,
            SalesTarget.periode.in_(months),
        )
    )
    target_value = float(target_value) if target_value else None
    capaian_pct = (total_penjualan / target_value * 100) if target_value and target_value > 0 else None
    sisa_target = max(0.0, (target_value or 0) - total_penjualan)

    # Agregasi penjualan dan target per cabang dalam satu query.
    penjualan_map = {
        c: float(t or 0)
        for c, t in db.execute(
            select(SalesPerformance.cabang, func.sum(SalesPerformance.penjualan))
            .where(SalesPerformance.periode.in_(months)).group_by(SalesPerformance.cabang)
        ).all()
    }
    target_map = {
        c: float(t or 0)
        for c, t in db.execute(
            select(SalesTarget.cabang, func.sum(SalesTarget.target))
            .where(
                SalesTarget.level == TargetLevel.CABANG,
                SalesTarget.periode.in_(months),
                SalesTarget.cabang.is_not(None),
            ).group_by(SalesTarget.cabang)
        ).all()
    }

    tercapai = 0
    belum: list[tuple[float, str]] = []
    for cabang, total_cabang in penjualan_map.items():
        t = target_map.get(cabang)
        if t and total_cabang >= t:
            tercapai += 1
        else:
            cap = (total_cabang / t * 100) if t and t > 0 else -1.0
            belum.append((cap, cabang))
    belum.sort()  # capaian terendah lebih dulu

    return PerusahaanPerformance(
        periode=periode,
        granularitas=granularitas,
        total_penjualan=total_penjualan,
        target_perusahaan=target_value,
        capaian_pct=capaian_pct,
        sisa_target=sisa_target,
        jumlah_cabang=len(penjualan_map),
        cabang_tercapai=tercapai,
        cabang_belum_tercapai=[c for _, c in belum],
    )


def _needs_notes(rec: EligibilityRecommendation, decision: str) -> bool:
    """Keputusan yang berbeda dari rekomendasi sistem wajib diberi catatan (PRD F32)."""
    return (
        (rec == EligibilityRecommendation.NOT_ELIGIBLE and decision == "Disetujui")
        or (rec == EligibilityRecommendation.ELIGIBLE and decision == "Ditolak")
        or (rec == EligibilityRecommendation.NO_TARGET and decision == "Disetujui")
    )


@router.patch("/assessment/{employee_id}/{periode}", dependencies=[PERFORMANCE])
def update_assessment(
    employee_id: str,
    periode: str,
    body: AssessmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Persetujuan Penilaian oleh akun Sales/Marketing penyetuju (PRD F32)"""
    # Cek apakah user adalah penyetuju
    if user.departemen != "Sales/Marketing" or not user.is_approver:
        raise HTTPException(403, "Hanya akun Sales/Marketing penyetuju yang dapat menyetujui penilaian")
    
    assessment = db.scalar(
        select(SalesAssessment).where(
            SalesAssessment.employee_id == employee_id,
            SalesAssessment.periode == periode
        )
    )
    
    if assessment is None:
        raise HTTPException(404, "Assessment tidak ditemukan")
    
    notes = (body.notes or "").strip() or None
    if _needs_notes(assessment.recommendation, body.status) and not notes:
        raise HTTPException(422, "Keputusan berbeda dari rekomendasi sistem. Catatan wajib diisi.")

    old_status = assessment.status.value
    assessment.status = AssessmentStatus(body.status)
    assessment.notes = notes

    if body.status == "Draf":
        assessment.approved_by = None
        assessment.approved_at = None
    else:
        assessment.approved_by = user.id
        assessment.approved_at = func.now()

    audit.log(db, user.id, "sales_assessment_update", {
        "employee_id": employee_id,
        "periode": periode,
        "old_status": old_status,
        "new_status": body.status,
        "notes": notes
    }, _ip(request))
    
    db.commit()
    
    return {"message": "Assessment berhasil diupdate"}
