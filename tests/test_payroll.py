import io

import polars as pl

from tests.conftest import as_dept
from tests.test_presensi import CSV_OK, HEADER, upload

# Data CSV_OK (lihat test_presensi). Hitungan manual untuk 2026-10-05 sampai 2026-10-09 (Senin sampai Jumat):
#   Ani : Hadir 3 hari (05, 06, 09), Izin 1, Alpa 1, terlambat 1 kali 10 menit, pulang cepat 1 kali 10 menit,
#         jam kerja 9,25 + 8,67 + 9,00 = 26,92, lembur 10 menit
#   Budi: Hadir 2 hari (05, 08), Dinas Luar 1, Sakit 1, Cuti 1, terlambat 1 kali 30 menit,
#         jam kerja 8,5 + 10,5 = 19,0, lembur 90 menit
RANGE = {"from": "2026-10-05", "to": "2026-10-09"}


def payroll(client, **params):
    r = client.get("/exports/payroll", params=params)
    assert r.status_code == 200, r.text
    return r, pl.read_excel(io.BytesIO(r.content), sheet_name="Rekap Payroll", engine="calamine")


def by_nik(df):
    return {row["nik"]: row for row in df.to_dicts()}


def test_payroll_matches_manual_calculation(client):
    upload(client)
    r, df = payroll(client, **RANGE)
    assert "rekap_payroll_2026-10-05_2026-10-09_DRAFT.xlsx" in r.headers["content-disposition"]
    assert df.height == 2
    ani, budi = by_nik(df)["001"], by_nik(df)["002"]   # NIK tetap teks, angka nol di depan tidak hilang

    assert (ani["hari_kerja_periode"], ani["hari_kerja_tercatat"], ani["hari_tanpa_data"]) == (5, 5, 0)
    assert (ani["hari_hadir"], ani["hari_dinas_luar"], ani["hari_izin"], ani["hari_sakit"], ani["hari_cuti"], ani["hari_alpa"]) == (3, 0, 1, 0, 0, 1)
    assert (ani["jumlah_terlambat"], ani["total_menit_terlambat"], ani["jumlah_pulang_cepat"], ani["total_menit_pulang_cepat"]) == (1, 10, 1, 10)
    assert ani["total_jam_kerja"] == 26.92 and ani["total_menit_lembur"] == 10 and ani["menit_lembur_hari_libur"] == 0
    assert ani["tingkat_kehadiran_persen"] == 60.0 and ani["status_periode"] == "DRAFT"

    assert (budi["hari_hadir"], budi["hari_dinas_luar"], budi["hari_sakit"], budi["hari_cuti"], budi["hari_alpa"]) == (2, 1, 1, 1, 0)
    assert (budi["jumlah_terlambat"], budi["total_menit_terlambat"], budi["jumlah_pulang_cepat"]) == (1, 30, 0)
    assert budi["total_jam_kerja"] == 19.0 and budi["total_menit_lembur"] == 90 and budi["tingkat_kehadiran_persen"] == 60.0


def test_payroll_flags_missing_days(client):
    upload(client)
    _, df = payroll(client, **{"from": "2026-10-05", "to": "2026-10-16"})   # dua minggu, baru satu minggu masuk
    ani = by_nik(df)["001"]
    assert (ani["hari_kerja_periode"], ani["hari_kerja_tercatat"], ani["hari_tanpa_data"]) == (11, 5, 6)   # 05-10 Okt (Senin-Sabtu) + 12-16 Okt = 11 hari kerja, Minggu tidak dihitung


def test_payroll_respects_holidays_after_recalculate(client):
    upload(client)
    client.post("/settings/holidays", json={"tanggal": "2026-10-06", "nama": "Libur uji"})
    client.post("/settings/recalculate", json={"from": "2026-10-01", "to": "2026-10-31"})
    _, df = payroll(client, **RANGE)
    ani = by_nik(df)["001"]
    assert (ani["hari_kerja_periode"], ani["hari_kerja_tercatat"], ani["hari_tanpa_data"]) == (4, 4, 0)
    assert ani["jumlah_terlambat"] == 0 and ani["jumlah_pulang_cepat"] == 0   # 06 Oktober (terlambat dan pulang cepat) kini hari libur


def test_weekend_work_is_reported_separately(client):
    upload(client, HEADER + "001,Ani,2026-10-11,IT,,08:00,19:00,Hadir,\n")   # Minggu, pulang 19:00
    _, df = payroll(client, **{"from": "2026-10-05", "to": "2026-10-11"})
    ani = by_nik(df)["001"]
    assert ani["total_menit_lembur"] == 120 and ani["menit_lembur_hari_libur"] == 120
    assert ani["total_jam_kerja"] == 11.0 and ani["jam_kerja_hari_libur"] == 11.0
    assert ani["hari_hadir"] == 0 and ani["hari_kerja_tercatat"] == 0   # Minggu bukan hari kerja


def test_payroll_ignores_status_filter_and_applies_division(client):
    upload(client)
    _, df = payroll(client, **RANGE, status="Terlambat")
    assert by_nik(df)["001"]["hari_izin"] == 1                      # status tidak menyaring baris
    _, df = payroll(client, **RANGE, departemen="HR")
    assert df["nik"].to_list() == ["002"]


def test_payroll_needs_range_and_hr_department(client):
    upload(client)
    assert client.get("/exports/payroll").status_code == 422
    assert client.get("/exports/payroll", params={"from": "2026-10-01"}).status_code == 422
    assert client.get("/exports/payroll", params={"from": "2025-01-01", "to": "2026-12-31"}).status_code == 422
    as_dept("IT")
    assert client.get("/exports/payroll", params=RANGE).status_code == 403
    as_dept("Sales/Marketing")
    assert client.get("/exports/payroll", params=RANGE).status_code == 403
    as_dept("Audit")  # Audit boleh lihat Rekap Payroll
    assert client.get("/exports/payroll", params=RANGE).status_code == 200
    as_dept("HR")
    assert client.get("/exports/payroll", params=RANGE).status_code == 200


def test_payroll_notes_sheet_documents_columns(client):
    upload(client)
    r, _ = payroll(client, **RANGE)
    notes = pl.read_excel(io.BytesIO(r.content), sheet_name="Catatan", engine="calamine", has_header=False)
    text = " ".join(str(v) for v in notes.to_series(0).to_list() + notes.to_series(1).to_list())
    assert "hari_tanpa_data" in text and "total_menit_lembur" in text and "DRAFT" in text


def test_standard_recap_now_includes_overtime_and_hours(client):
    upload(client)
    r = client.get("/exports/excel", params=RANGE)
    emp = pl.read_excel(io.BytesIO(r.content), sheet_name="Rekap per Karyawan", engine="calamine")
    for col in ["Pulang Cepat", "Total Menit Pulang Cepat", "Total Jam Kerja", "Total Menit Lembur"]:
        assert col in emp.columns
    ani = emp.filter(pl.col("NIK") == "001").row(0, named=True)
    assert ani["Total Menit Lembur"] == 10 and ani["Total Jam Kerja"] == 26.92 and ani["Pulang Cepat"] == 1
    dept = pl.read_excel(io.BytesIO(r.content), sheet_name="Rekap per Divisi", engine="calamine")
    assert dept.filter(pl.col("Divisi") == "HR").row(0, named=True)["Total Menit Lembur"] == 90


# ---------- Pembagi hari kerja payroll (PRD F29) ----------

def test_payroll_workday_divisor_setting(client):
    upload(client)
    # Default 25
    assert client.get("/settings/payroll").json() == {"payroll_workdays": 25}
    _, df = payroll(client, **RANGE)
    ani = by_nik(df)["001"]
    assert ani["pembagi_hari_kerja"] == 25
    # Ani: hadir 3 + izin 1 = 4 hari dibayar (alpa tidak dibayar); 4/25*100 = 16.0
    assert ani["hari_dibayar"] == 4 and ani["gaji_prorata_persen"] == 16.0
    # Budi: hadir 2 + dinas 1 + sakit 1 + cuti 1 = 5 hari; 5/25*100 = 20.0
    assert by_nik(df)["002"]["hari_dibayar"] == 5 and by_nik(df)["002"]["gaji_prorata_persen"] == 20.0

    # Ubah pembagi ke 22 -> persen berubah
    assert client.put("/settings/payroll", json={"payroll_workdays": 22}).json() == {"payroll_workdays": 22}
    _, df = payroll(client, **RANGE)
    ani = by_nik(df)["001"]
    assert ani["pembagi_hari_kerja"] == 22 and ani["gaji_prorata_persen"] == round(4 * 100 / 22, 2)


def test_payroll_divisor_validation_and_access(client):
    assert client.put("/settings/payroll", json={"payroll_workdays": 0}).status_code == 422
    assert client.put("/settings/payroll", json={"payroll_workdays": 40}).status_code == 422
    as_dept("Audit")  # Audit boleh lihat payroll tapi tidak mengubah pengaturan
    assert client.get("/settings/payroll").status_code == 403
    as_dept("HR")
    assert client.put("/settings/payroll", json={"payroll_workdays": 25}).status_code == 200
