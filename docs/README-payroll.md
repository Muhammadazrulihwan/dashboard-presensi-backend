# Pembaruan: Rekap Payroll dan kolom lembur

## Yang berubah
- Endpoint baru `GET /exports/payroll?from=YYYY-MM-DD&to=YYYY-MM-DD[&departemen=][&karyawan=]` (hanya HR Admin).
  Menghasilkan Excel dengan sheet **Rekap Payroll** (satu baris per karyawan, angka polos) dan sheet **Catatan** (arti tiap kolom).
  Rentang tanggal wajib diisi (maksimal 1 tahun). Filter status diabaikan agar semua status ikut terhitung.
- Sheet Rekap per Karyawan dan Rekap per Divisi pada Export Excel kini memuat pulang cepat, total jam kerja, dan total menit lembur.
  Header "Hadir" menjadi "Hadir (termasuk Dinas Luar)" agar tidak membingungkan dengan kolom Dinas Luar.
- Dashboard: tombol **Rekap Payroll** muncul untuk HR Admin bila filter memiliki tanggal awal dan akhir.
- `tests/test_flow.py` (sisa versi awal) dihapus. Tes baru: `tests/test_payroll.py`. Total 40 tes.
- `requirements.txt` kini memuat `pyjwt`, `.env.example` memuat variabel login.

## Kolom Rekap Payroll
nik, nama, departemen, periode_awal, periode_akhir, hari_kerja_periode, hari_kerja_tercatat, hari_tanpa_data,
hari_hadir, hari_dinas_luar, hari_izin, hari_sakit, hari_cuti, hari_alpa, jumlah_terlambat, total_menit_terlambat,
jumlah_pulang_cepat, total_menit_pulang_cepat, total_jam_kerja, jam_kerja_hari_libur, total_menit_lembur,
menit_lembur_hari_libur, tingkat_kehadiran_persen, status_periode.

Catatan: status_periode selalu DRAFT sampai fitur Tutup periode dibuat.
