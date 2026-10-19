from tests.test_presensi import HEADER, upload

# 2026-10-11 Minggu: Ani masuk 08:45 (bukan hari kerja), 2026-10-05 Senin: Ani masuk 08:30 (terlambat 30).
CSV = HEADER + "\n".join([
    "001,Ani,2026-10-05,IT,,08:30,17:00,Hadir,",
    "001,Ani,2026-10-11,IT,,08:45,16:00,Hadir,",
]) + "\n"
RANGE = {"from": "2026-10-05", "to": "2026-10-11"}


def test_lateness_ignored_on_non_workdays(client):
    upload(client, CSV)
    s = client.get("/dashboard/summary", params=RANGE).json()["current"]
    assert s["terlambat"] == 1 and s["menit_terlambat"] == 30  # hanya Senin

    rows = {r["tanggal"][:10]: r for r in client.get("/dashboard/records", params={**RANGE, "page_size": 50}).json()["items"]}
    assert rows["2026-10-11"]["menit_terlambat"] == 0 and rows["2026-10-11"]["status"] == "Hadir"

    trend = {t["period"]: t for t in client.get("/dashboard/trend", params={**RANGE, "granularity": "day"}).json()}
    assert trend["2026-10-11"]["terlambat"] == 0 and trend["2026-10-11"]["kehadiran_pct"] is None  # libur: tanpa batang
    assert trend["2026-10-05"]["terlambat"] == 1

    comp = {c["status"]: c["total"] for c in client.get("/dashboard/status-composition", params=RANGE).json()}
    assert comp["Terlambat"] == s["terlambat"]  # kartu KPI dan komposisi status kini selaras


def test_saturday_is_a_workday_sunday_is_not(client):
    """Perusahaan bekerja Senin sampai Sabtu (25 hari kerja per bulan pada rekap gaji): hanya Minggu libur."""
    upload(client, HEADER + "\n".join([
        "001,Ani,2026-10-10,IT,,08:40,17:00,Hadir,",     # Sabtu: hari kerja, terlambat 40 menit
        "001,Ani,2026-10-11,IT,,,,,",                    # Minggu: kosong = libur, bukan alpa
        "002,Budi,2026-10-10,IT,,,,,",                   # Sabtu kosong = alpa
    ]) + "\n")
    rows = {(r["nik"], r["tanggal"][:10]): r for r in client.get("/dashboard/records", params={**RANGE, "page_size": 50}).json()["items"]}
    assert rows[("001", "2026-10-10")]["menit_terlambat"] == 40
    assert rows[("002", "2026-10-10")]["status"] == "Alpa"
    assert rows[("001", "2026-10-11")]["status"] == "Libur"
