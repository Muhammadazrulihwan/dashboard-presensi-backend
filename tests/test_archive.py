"""Arsip file upload (PRD F22-F24): checksum, pencarian, tempat sampah, pulihkan, hapus permanen."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import ArchiveStatus, ArchivedFile, ArchivedFileEmployee
from tests.conftest import as_dept
from tests.test_presensi import upload


@pytest.fixture(autouse=True)
def _clean_archive():
    with SessionLocal() as db:
        # Hapus tabel anak lebih dulu: SQLite tidak menegakkan ON DELETE CASCADE secara default,
        # sehingga baris anak akan tertinggal dan membuat id arsip baru bentrok pada unique constraint.
        db.query(ArchivedFileEmployee).delete()
        db.query(ArchivedFile).delete()
        db.commit()
    yield


def _only_archive() -> ArchivedFile:
    with SessionLocal() as db:
        return db.scalar(select(ArchivedFile).order_by(ArchivedFile.id.desc()))


def test_upload_creates_archive_with_checksum(client):
    upload(client)  # tes presensi: 11 baris, 2 karyawan
    row = _only_archive()
    assert row is not None and row.jenis_sumber == "presensi"
    assert len(row.checksum) == 64 and row.status == ArchiveStatus.aktif
    assert row.periode_awal.isoformat() == "2026-10-05" and row.periode_akhir.isoformat() == "2026-10-11"

    page = client.get("/archive").json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["checksum_terverifikasi"] is True and item["status"] == "aktif"
    assert item["bisa_dihapus_permanen"] is False


def test_archive_search_and_download(client):
    upload(client)
    assert client.get("/archive", params={"q": "presensi"}).json()["total"] == 1
    assert client.get("/archive", params={"q": "tidak-ada"}).json()["total"] == 0
    assert client.get("/archive", params={"jenis_sumber": "penjualan"}).json()["total"] == 0
    assert client.get("/archive", params={"dari": "2026-10-01", "sampai": "2026-10-31"}).json()["total"] == 1

    fid = client.get("/archive").json()["items"][0]["id"]
    r = client.get(f"/archive/{fid}/download")
    assert r.status_code == 200 and r.content.startswith(b"nik,")  # isi file asli (CSV) utuh
    assert "attachment" in r.headers["content-disposition"]


def test_archive_search_by_employee_id(client):
    """PRD F23: arsip bisa dicari berdasarkan Employee ID yang tercatat di file."""
    upload(client)  # CSV_OK berisi NIK 001 (Ani) dan 002 (Budi)

    page = client.get("/archive").json()
    assert page["total"] == 1
    item = page["items"][0]
    assert sorted(item["employee_ids"]) == ["001", "002"]

    # Cocok presisi per karyawan
    assert client.get("/archive", params={"employee_id": "001"}).json()["total"] == 1
    assert client.get("/archive", params={"employee_id": "002"}).json()["total"] == 1
    # Tidak ada yang cocok
    assert client.get("/archive", params={"employee_id": "999"}).json()["total"] == 0
    # Presisi, bukan substring: "00" tidak boleh mencocokkan "001"/"002"
    assert client.get("/archive", params={"employee_id": "00"}).json()["total"] == 0
    # Pencarian ID tidak peka huruf besar-kecil dan spasi di tepi diabaikan
    assert client.get("/archive", params={"employee_id": " 001 "}).json()["total"] == 1


def test_trash_restore_and_purge_flow(client):
    upload(client)
    fid = client.get("/archive").json()["items"][0]["id"]

    # Pindah ke tempat sampah butuh alasan
    assert client.post(f"/archive/{fid}/trash", json={"alasan": ""}).status_code == 422
    r = client.post(f"/archive/{fid}/trash", json={"alasan": "Salah unggah"})
    assert r.status_code == 200 and r.json()["status"] == "sampah"
    assert r.json()["alasan_hapus"] == "Salah unggah"

    # File sampah hilang dari daftar aktif, muncul di tab sampah
    assert client.get("/archive").json()["total"] == 0
    assert client.get("/archive", params={"status": "sampah"}).json()["total"] == 1
    # Tidak bisa dipindah ke sampah dua kali
    assert client.post(f"/archive/{fid}/trash", json={"alasan": "lagi"}).status_code == 409

    # Pulihkan
    assert client.post(f"/archive/{fid}/restore").json()["status"] == "aktif"
    assert client.get("/archive").json()["total"] == 1


def test_purge_requires_trash_and_waiting_period(client):
    upload(client)
    fid = client.get("/archive").json()["items"][0]["id"]

    # Belum di tempat sampah: tidak bisa hapus permanen
    assert client.delete(f"/archive/{fid}").status_code == 409

    client.post(f"/archive/{fid}/trash", json={"alasan": "uji"})
    # Masa tunggu belum habis
    assert client.delete(f"/archive/{fid}").status_code == 409

    # Mundurkan waktu hapus agar masa tunggu lewat
    with SessionLocal() as db:
        row = db.get(ArchivedFile, fid)
        row.dihapus_at = datetime.now(timezone.utc) - timedelta(days=400)
        db.commit()

    assert client.get("/archive", params={"status": "sampah"}).json()["items"][0]["bisa_dihapus_permanen"] is True
    r = client.delete(f"/archive/{fid}")
    assert r.status_code == 200 and r.json()["status"] == "dihapus"
    # File fisik hilang -> tidak bisa diunduh lagi
    assert client.get(f"/archive/{fid}/download").status_code == 410


def test_archive_access_matrix(client):
    upload(client)
    fid = client.get("/archive").json()["items"][0]["id"]

    as_dept("Audit")  # Audit: lihat dan unduh, tidak hapus
    assert client.get("/archive").status_code == 200
    assert client.get(f"/archive/{fid}/download").status_code == 200
    assert client.post(f"/archive/{fid}/trash", json={"alasan": "x"}).status_code == 403

    as_dept("IT")
    assert client.get("/archive").status_code == 403
    as_dept("Sales/Marketing")
    assert client.get("/archive").status_code == 403
    as_dept("HR")
    assert client.get("/archive").status_code == 200


def test_actions_are_audited(client):
    from app.models import AuditLog

    upload(client)
    fid = client.get("/archive").json()["items"][0]["id"]
    client.get(f"/archive/{fid}/download")
    client.post(f"/archive/{fid}/trash", json={"alasan": "audit uji"})
    client.post(f"/archive/{fid}/restore")
    with SessionLocal() as db:
        actions = [r.action for r in db.scalars(select(AuditLog).order_by(AuditLog.id)).all()]
    assert "archive_download" in actions and "archive_trash" in actions and "archive_restore" in actions
