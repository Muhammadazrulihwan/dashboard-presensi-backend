"""Kelengkapan Data (PRD F12): hari tanpa data, clock out/in hilang, cakupan bulan, hak akses HR."""
from tests.conftest import as_dept
from tests.test_presensi import HEADER, RANGE, upload

# 2026-10-05 Senin sampai 2026-10-09 Jumat = 5 hari kerja.
# Ani tercatat 5 hari, tetapi 10-06 tidak punya jam keluar (clock out hilang).
# Budi hanya tercatat 10-05 (clock in hilang), sisa hari kerja tidak ada datanya.
# Status Dinas Luar dipakai pada baris yang hanya punya satu ketukan karena validator upload
# mewajibkan jam masuk dan jam keluar untuk status Hadir (keterbatasan template saat ini).
CSV = HEADER + "\n".join([
    "001,Ani,2026-10-05,IT,,07:55,17:10,Hadir,",
    "001,Ani,2026-10-06,IT,,08:10,,Dinas Luar,",   # clock out hilang
    "001,Ani,2026-10-07,IT,,,,Izin,Urusan keluarga",
    "001,Ani,2026-10-08,IT,,,,,",
    "001,Ani,2026-10-09,IT,,08:00,17:00,Hadir,",
    "002,Budi,2026-10-05,HR,,,17:00,Dinas Luar,",   # clock in hilang
]) + "\n"


def test_completeness_counts(client):
    upload(client, CSV)
    r = client.get("/dashboard/completeness", params=RANGE)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["periode"] == {"from": "2026-10-05", "to": "2026-10-09"}
    assert body["hari_kerja_kalender"] == 5
    rs = body["ringkasan"]
    assert rs["karyawan"] == 2
    assert rs["baris_clock_out_hilang"] == 1
    assert rs["baris_clock_in_hilang"] == 1

    # Ani lengkap 5 hari kerja; Budi hanya 1 hari kerja tercatat -> 4 hari tanpa data.
    assert [x["nik"] for x in body["karyawan_tanpa_data"]] == ["002"]
    assert body["karyawan_tanpa_data"][0]["hari_tanpa_data"] == 4
    assert rs["karyawan_lengkap"] == 1 and rs["karyawan_tanpa_data"] == 1
    assert rs["hari_tanpa_data"] == 4

    out = body["clock_out_hilang"][0]
    assert out["nik"] == "001" and out["tanggal"] == "2026-10-06"
    assert out["jam_masuk"] == "08:10" and out["jam_keluar"] is None

    inc = body["clock_in_hilang"][0]
    assert inc["nik"] == "002" and inc["jam_masuk"] is None and inc["jam_keluar"] == "17:00"

    assert body["cakupan_bulan"] == [{"periode": "2026-10", "karyawan": 2, "baris": 6, "hari_kerja": 6}]

    sumber = {s["kode"]: s for s in body["sumber"]}
    # Data diunggah lewat template satu file, jadi hanya "presensi" yang tersedia.
    # Keempat sumber paket ditandai "belum" (F12: deteksi sumber belum masuk yang sebenarnya).
    assert sumber["presensi"]["status"] == "tersedia"
    for kode in ("activity", "report", "timeoff", "approval"):
        assert sumber[kode]["status"] == "belum", kode
    # Indikator baru ada di respons (kosong pada data template lama).
    assert body["selisih_report"] == [] and body["koreksi_setelah_tutup"] == []
    assert body["ringkasan"]["baris_selisih_report"] == 0


def test_completeness_hr_only(client):
    upload(client, CSV)
    for dept in ("IT", "Audit", "Sales/Marketing"):
        as_dept(dept)
        assert client.get("/dashboard/completeness", params=RANGE).status_code == 403, dept
    as_dept("HR")
    assert client.get("/dashboard/completeness", params=RANGE).status_code == 200


def test_completeness_empty_range(client):
    # Tanpa data sama sekali: tetap 200 dengan angka nol, bukan error.
    r = client.get("/dashboard/completeness", params={"from": "2030-01-01", "to": "2030-01-31"})
    assert r.status_code == 200
    body = r.json()
    assert body["ringkasan"]["karyawan"] == 0 and body["karyawan_tanpa_data"] == []


def test_completeness_uses_data_range_when_no_filter(client):
    upload(client, CSV)
    body = client.get("/dashboard/completeness").json()
    assert body["periode"]["from"] == "2026-10-05" and body["periode"]["to"] == "2026-10-09"
