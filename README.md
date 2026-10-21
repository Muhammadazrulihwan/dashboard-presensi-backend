# Backend Dashboard Presensi (FastAPI)

## Setup (PowerShell, Python 3.11 atau lebih baru)
```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head        # buat/perbarui skema database
uvicorn app.main:app --reload --port 8000
```
Dokumentasi interaktif: http://localhost:8000/docs

Skema database dikelola Alembic (`alembic/`). Jalankan `alembic upgrade head` sebelum memulai server, dan
`alembic revision --autogenerate -m "..."` setelah mengubah `app/models.py`.

Tanpa file `.env`, data disimpan di SQLite (`dev.db`). Untuk PostgreSQL: `docker compose up -d`, salin `.env.example` menjadi `.env`.

## Tes
```powershell
pytest
```
Default tes memakai SQLite sementara (cepat, tanpa setup). Untuk menguji perilaku MySQL 8.0 yang sesungguhnya
(PRD Bab 9), arahkan tes ke database uji terpisah lewat `TEST_DATABASE_URL`. Database itu dibuat kosong dan
skemanya dibangun/di-reset otomatis lewat Alembic setiap sesi tes.

```powershell
# 1. Buat database uji (contoh memakai MySQL lokal)
mysql -u root -e "CREATE DATABASE presensi_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

# 2. Jalankan tes terhadap MySQL
$env:TEST_DATABASE_URL="mysql+pymysql://presensi_app:PASSWORD@127.0.0.1:3306/presensi_test?charset=utf8mb4"
pytest
```

Jangan mengarahkan `TEST_DATABASE_URL` ke database berisi data nyata: tes menurunkan dan membangun ulang skema.

## Migrasi
```powershell
alembic upgrade head       # naik ke versi terbaru
alembic downgrade base     # turunkan semua (untuk uji bersih)
alembic current            # versi aktif
```

## Endpoint
| Method | Path | Peran | Keterangan |
| --- | --- | --- | --- |
| POST | /auth/login, /auth/logout, GET /auth/me | semua | sesi cookie httpOnly |
| GET | /uploads/template | HR | template XLSX presensi |
| POST | /uploads/preview | HR | upload, validasi, hitung, deteksi bentrok |
| POST | /uploads/{id}/confirm | HR | simpan, body `{"mode": "overwrite" atau "skip"}` jika ada bentrok |
| GET | /uploads | HR | riwayat (`status`, `limit`, `offset`) |
| DELETE | /uploads/{id} | HR | hapus upload beserta data presensinya |
| GET/PUT/DELETE | /settings/shifts, /settings/shifts/{kode} | HR | jam kerja dan toleransi per shift |
| GET/POST/DELETE | /settings/holidays | HR | hari libur dan cuti bersama |
| POST | /settings/recalculate | HR | hitung ulang periode dengan aturan terbaru |
| GET | /dashboard/meta, /summary, /trend, /status-composition, /by-department | semua | agregat |
| GET | /dashboard/top-employees, /records, /employee/{nik} | HR, atasan | data per orang |
| GET/POST/PATCH/DELETE | /admin/users, /admin/users/{id} | HR | kelola user |
| GET | /exports/excel, /exports/pdf | HR, atasan | mengikuti filter aktif |
| GET | /exports/employee/pdf | HR, atasan | rekap individual, satu karyawan per halaman (`nik` opsional) |
| GET | /exports/payroll | HR | rekap siap payslip |

Filter dashboard dan export: `from`, `to` (YYYY-MM-DD), `departemen`, `status` (termasuk "Terlambat"), `karyawan` (NIK atau nama).

## Aturan hitung (PRD bagian 7)
- menit_terlambat = max(0, jam masuk - jam mulai shift - toleransi), hanya untuk status Hadir.
- menit_pulang_cepat = max(0, jam selesai - jam keluar). menit_lembur = max(0, jam keluar - jam selesai).
- durasi_kerja = jam keluar - jam masuk (jam). Shift lintas hari (mis. 22:00 sampai 06:00) otomatis dikenali dari jam shift.
- Hari kerja = bukan Sabtu, Minggu, atau hari libur. Status kosong tanpa jam masuk: Alpa di hari kerja, Libur di hari non-kerja.
- Tingkat kehadiran = (Hadir + Dinas Luar) pada hari kerja dibagi seluruh hari kerja efektif.

## Peran
Aturan peran berjalan di API: HR Admin penuh, atasan dibatasi ke divisinya, manajemen hanya agregat (tanpa data per orang),
dan diuji lewat tes. Akun admin awal dibuat dari `ADMIN_EMAIL`/`ADMIN_PASSWORD` saat startup bila belum ada.

