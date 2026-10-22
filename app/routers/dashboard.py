from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import DASHBOARD, HR_ONLY, PERFORMANCE, data_scope, get_current_user, scoped_filters
from app.models import Department, User
from app.schemas import (
    CompletenessResponse, DeptRow, EmployeeDetail, MetaOut, PerformanceRow, RecordsPage, StatusCount, SummaryResponse,
    TopEmployee, TrendPoint,
)
from app.services import aggregator as agg
from app.services import completeness as comp
from app.services.rules import min_to_hhmm

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
agg_roles = DASHBOARD
detail_roles = PERFORMANCE  # penilaian per orang (top-employees, detail karyawan): HR, Audit, Sales/Marketing


@router.get("/meta", response_model=MetaOut, dependencies=[agg_roles])
def get_meta(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return agg.meta(db, data_scope(user))


@router.get("/summary", response_model=SummaryResponse, dependencies=[agg_roles])
def get_summary(f: agg.Filters = Depends(scoped_filters), db: Session = Depends(get_db)):
    prev = agg.previous_filters(f)
    return {"current": agg.summary(db, f), "previous": agg.summary(db, prev) if prev else None}


@router.get("/trend", response_model=list[TrendPoint], dependencies=[agg_roles])
def get_trend(
    granularity: Literal["day", "month"] = "day",
    f: agg.Filters = Depends(scoped_filters),
    db: Session = Depends(get_db),
):
    return agg.trend(db, f, granularity)


@router.get("/status-composition", response_model=list[StatusCount], dependencies=[agg_roles])
def get_status_composition(f: agg.Filters = Depends(scoped_filters), db: Session = Depends(get_db)):
    return agg.status_composition(db, f)


@router.get("/by-department", response_model=list[DeptRow], dependencies=[agg_roles])
def get_by_department(f: agg.Filters = Depends(scoped_filters), db: Session = Depends(get_db)):
    return agg.recap(db, f, "departemen")


@router.get("/top-employees", response_model=list[TopEmployee], dependencies=[detail_roles])
def get_top_employees(
    metric: Literal["terlambat", "alpa"] = "terlambat",
    limit: int = Query(5, ge=1, le=50),
    f: agg.Filters = Depends(scoped_filters),
    db: Session = Depends(get_db),
):
    return agg.top_employees(db, f, metric, limit)


@router.get("/records", response_model=RecordsPage, dependencies=[agg_roles])  # IT boleh lihat daftar (PRD matriks akses)
def get_records(
    f: agg.Filters = Depends(scoped_filters),
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    sort: Literal["tanggal", "nik", "nama", "departemen", "jam_masuk", "menit_terlambat", "durasi_kerja", "status"] = "tanggal",
    order: Literal["asc", "desc"] = "asc",
    q: str | None = None,
    db: Session = Depends(get_db),
):
    rows, total = agg.records_page(db, f, page, page_size, sort, order, q)
    items = [
        {
            "id": r.id, "tanggal": r.tanggal, "nik": r.nik, "nama": r.nama, "departemen": r.departemen,
            "jam_masuk": min_to_hhmm(r.jam_masuk_menit), "jam_keluar": min_to_hhmm(r.jam_keluar_menit),
            "menit_terlambat": r.menit_terlambat, "durasi_kerja": r.durasi_kerja, "status": r.status, "keterangan": r.keterangan,
            "jam_kerja_nyata": r.jam_kerja_nyata, "dikoreksi": bool(r.dikoreksi),
            "koreksi_alasan": r.koreksi_alasan, "koreksi_approver": r.koreksi_approver,
            "clock_out_hilang": bool(r.clock_out_hilang), "selisih_report": r.selisih_report,
            "sumber": r.sumber, "jumlah_ketukan": r.jumlah_ketukan, "clock_in_hilang": bool(r.clock_in_hilang),
            "izin_kode": r.izin_kode, "izin_nama": r.izin_nama, "izin_tipe": r.izin_tipe,
        }
        for r in rows
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/employee/{nik}", response_model=EmployeeDetail, dependencies=[detail_roles])
def get_employee_detail(nik: str, f: agg.Filters = Depends(scoped_filters), db: Session = Depends(get_db)):
    detail = agg.employee_detail(db, f, nik)
    if detail is None:
        raise HTTPException(404, "Karyawan tidak ditemukan pada rentang filter ini")
    return detail


@router.get("/performance", response_model=list[PerformanceRow], dependencies=[detail_roles])
def get_performance(
    sort: Literal[
        "nama", "departemen", "kehadiran_pct", "tepat_waktu_pct", "terlambat",
        "menit_terlambat", "alpa", "jam_kerja", "menit_lembur",
    ] = "kehadiran_pct",
    order: Literal["asc", "desc"] = "desc",
    f: agg.Filters = Depends(scoped_filters),
    db: Session = Depends(get_db),
):
    """Peringkat kinerja per karyawan (PRD F26): HR, Audit, Sales/Marketing. IT tidak melihat."""
    return agg.performance_ranking(db, f, sort, order)


@router.get("/completeness", response_model=CompletenessResponse, dependencies=[HR_ONLY])
def get_completeness(f: agg.Filters = Depends(scoped_filters), db: Session = Depends(get_db)):
    """Kelengkapan Data (PRD F12): hari tanpa data, clock out/in hilang, cakupan bulan, status sumber.

    Hanya HR. Indikator selisih Report Attendance dan koreksi setelah tutup bulan menyusul di Rilis B
    karena membutuhkan parser empat sumber.
    """
    return comp.completeness(db, f)
