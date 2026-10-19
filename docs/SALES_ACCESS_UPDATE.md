# Hak Akses Kinerja Penjualan (Rilis E)

## Updated: 16 Oktober 2026

### Perubahan dari PRD v0.6:
Upload Kinerja Penjualan dipindahkan dari **HR** ke **Sales/Marketing** untuk separation of concern yang lebih baik.

---

## Hak Akses Matrix

| Fitur | HR | Sales/Marketing | Sales/Marketing (Penyetuju) | Audit | IT |
|-------|----|-----------------|-----------------------------|-------|-----|
| **Upload Presensi** | ✅ | ❌ | ❌ | ❌ | ❌ |
| **Upload Kinerja Penjualan** | ❌ | ✅ | ✅ | ❌ | ❌ |
| **Lihat Dashboard Penjualan** | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Approve/Reject Penilaian** | ❌ | ❌ | ✅ | ❌ | ❌ |
| **Kelola User** | ❌ | ❌ | ❌ | ❌ | ✅ |

---

## Logika Bisnis

### Upload Kinerja Penjualan
- **Siapa:** Sales/Marketing (semua akun)
- **File:** Excel dengan 2 sheet (Penjualan + Target)
- **Frekuensi:** Bulanan (setelah tutup buku penjualan)
- **Auto-process:** Sistem otomatis buat rekomendasi kelayakan per salesman

### Persetujuan Penilaian
- **Siapa:** Hanya Sales/Marketing dengan flag `is_approver = true`
- **Fungsi:** Review rekomendasi sistem dan approve/reject
- **Status:** Draf → Disetujui / Ditolak

### Dashboard
- **Akses:** HR, Audit, Sales/Marketing
- **3 Tab:**
  1. **Salesman** - detail per salesman dengan target & capaian
  2. **Cabang** - peringkat antar cabang, sisa target
  3. **Perusahaan** - overview total perusahaan

---

## API Endpoints

### Upload
- `POST /sales/preview` - Preview & validasi (Sales/Marketing)
- `POST /sales/{upload_id}/confirm` - Simpan data (Sales/Marketing)

### Dashboard  
- `GET /sales/salesman?periode=2026-09&cabang=Jakarta` (HR, Audit, Sales/Marketing)
- `GET /sales/cabang?periode=2026-09` (HR, Audit, Sales/Marketing)
- `GET /sales/perusahaan?periode=2026-09` (HR, Audit, Sales/Marketing)

### Assessment
- `PATCH /sales/assessment/{employee_id}/{periode}` (Sales/Marketing Penyetuju only)

---

## Alasan Perubahan

**Sebelumnya (PRD v0.6):**
- HR upload semua (presensi + penjualan)

**Sekarang (Updated):**
- **HR** → Upload presensi, lihat hasil akhir penjualan untuk hitung gaji
- **Sales/Marketing** → Upload data penjualan (mereka yang punya data source)
- **Separation of Concern:** Tiap departemen handle data mereka sendiri

Lebih sesuai prinsip ownership & responsibility! ✅
