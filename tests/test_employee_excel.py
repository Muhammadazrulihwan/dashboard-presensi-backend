"""Export Excel rekap individu (PRD F21): hak akses, isi sheet, dan batas data."""
import io

import polars as pl

from tests.conftest import as_dept
from tests.test_presensi import RANGE, upload


def _sheet(content: bytes, name: str, header: bool = True) -> pl.DataFrame:
    return pl.read_excel(io.BytesIO(content), sheet_name=name, engine="calamine", has_header=header)


def test_employee_excel_content(client):
    upload(client)
    r = client.get("/exports/employee/excel", params={**RANGE, "nik": "001"})
    assert r.status_code == 200
    assert "rekap_karyawan_001.xlsx" in r.headers["content-disposition"]

    rekap = _sheet(r.content, "Rekap", header=False)
    # Kolom nilai bercampur teks dan angka, jadi dibaca sebagai teks: bandingkan sebagai angka lewat float().
    kv = {str(a): b for a, b in zip(rekap.to_series(0).to_list(), rekap.to_series(1).to_list()) if a is not None}
    assert kv["NIK"] == "001" and kv["Nama"] == "Ani" and kv["Divisi"] == "IT"
    n = lambda k: float(kv[k])  # noqa: E731
    assert n("Hari kerja tercatat") == 5 and n("Hadir (termasuk dinas luar)") == 3
    assert n("Terlambat (kali)") == 1 and n("Total menit terlambat") == 10
    assert n("Pulang cepat (kali)") == 1 and n("Total menit lembur") == 10
    assert n("Izin") == 1 and n("Alpa") == 1

    harian = _sheet(r.content, "Data Harian")
    assert harian.height == 5 and "Menit terlambat" in harian.columns


def test_employee_excel_access_and_scope(client):
    upload(client)
    assert client.get("/exports/employee/excel", params=RANGE).status_code == 422                      # nik wajib
    assert client.get("/exports/employee/excel", params={**RANGE, "nik": "999"}).status_code == 404
    as_dept("Audit")
    assert client.get("/exports/employee/excel", params={**RANGE, "nik": "002"}).status_code == 200
    as_dept("Sales/Marketing")  # data uji berisi karyawan IT dan HR, di luar batas Sales/Marketing
    assert client.get("/exports/employee/excel", params={**RANGE, "nik": "001"}).status_code == 404
    as_dept("IT")
    assert client.get("/exports/employee/excel", params={**RANGE, "nik": "001"}).status_code == 403   # IT tidak menilai kinerja
    as_dept(None)
    assert client.get("/exports/employee/excel", params={**RANGE, "nik": "001"}).status_code == 403
