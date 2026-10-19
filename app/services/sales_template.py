"""Generate template Excel untuk upload Kinerja Penjualan

Template memiliki 2 sheet:
1. Penjualan - Data penjualan per salesman
2. Target - Target 3 tingkat (Perusahaan, Cabang, Salesman)
"""
import xlsxwriter
from io import BytesIO

def create_sales_template() -> BytesIO:
    """Buat template Excel Kinerja Penjualan dengan 2 sheet dan contoh data"""
    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    
    # Format
    header = wb.add_format({"bold": True, "bg_color": "#4472C4", "font_color": "white", "border": 1})
    text = wb.add_format({"num_format": "@", "border": 1})
    number = wb.add_format({"num_format": "#,##0", "border": 1})
    
    # ========== Sheet 1: Penjualan ==========
    ws_penjualan = wb.add_worksheet("Penjualan")
    
    penjualan_cols = [
        ("employee_id", 15, "NIK/ID Karyawan (wajib)"),
        ("nama", 25, "Nama Salesman (wajib)"),
        ("cabang", 20, "Nama Cabang (wajib)"),
        ("periode", 12, "Format YYYY-MM (wajib)"),
        ("penjualan", 15, "Total Penjualan (wajib)"),
        ("penerimaan", 15, "Total Penerimaan (opsional)"),
        ("piutang_outlet", 15, "Piutang Outlet (opsional)"),
        ("sisa_bad_debt", 15, "Sisa Bad Debt (opsional)")
    ]
    
    # Header
    for i, (col, width, _) in enumerate(penjualan_cols):
        ws_penjualan.write(0, i, col, header)
        ws_penjualan.set_column(i, i, width)
    
    # Sample data
    samples_penjualan = [
        ["S001", "Ahmad Rizki", "Jakarta", "2026-09", 45000000, 40000000, 5000000, 1000000],
        ["S002", "Siti Nurhaliza", "Jakarta", "2026-09", 38000000, 35000000, 3000000, 500000],
        ["S003", "Budi Santoso", "Bandung", "2026-09", 52000000, 50000000, 2000000, 0],
    ]
    
    for r, row in enumerate(samples_penjualan, start=1):
        ws_penjualan.write_string(r, 0, row[0], text)
        ws_penjualan.write_string(r, 1, row[1], text)
        ws_penjualan.write_string(r, 2, row[2], text)
        ws_penjualan.write_string(r, 3, row[3], text)
        for c in range(4, 8):
            ws_penjualan.write_number(r, c, row[c], number)
    
    # ========== Sheet 2: Target ==========
    ws_target = wb.add_worksheet("Target")
    
    target_cols = [
        ("level", 15, "Perusahaan/Cabang/Salesman (wajib)"),
        ("cabang", 20, "Nama Cabang (untuk level Cabang & Salesman)"),
        ("employee_id", 15, "NIK (hanya untuk level Salesman)"),
        ("periode", 12, "Format YYYY-MM (wajib)"),
        ("target_penjualan", 18, "Target Penjualan (wajib)")
    ]
    
    # Header
    for i, (col, width, _) in enumerate(target_cols):
        ws_target.write(0, i, col, header)
        ws_target.set_column(i, i, width)
    
    # Sample data - 3 tingkat target
    samples_target = [
        # Target Perusahaan
        ["Perusahaan", "", "", "2026-09", 500000000],
        # Target Cabang
        ["Cabang", "Jakarta", "", "2026-09", 200000000],
        ["Cabang", "Bandung", "", "2026-09", 150000000],
        ["Cabang", "Surabaya", "", "2026-09", 150000000],
        # Target Salesman
        ["Salesman", "Jakarta", "S001", "2026-09", 40000000],
        ["Salesman", "Jakarta", "S002", "2026-09", 35000000],
        ["Salesman", "Bandung", "S003", "2026-09", 50000000],
    ]
    
    for r, row in enumerate(samples_target, start=1):
        ws_target.write_string(r, 0, row[0], text)
        ws_target.write_string(r, 1, row[1], text)
        ws_target.write_string(r, 2, row[2], text)
        ws_target.write_string(r, 3, row[3], text)
        ws_target.write_number(r, 4, row[4], number)
    
    # ========== Sheet 3: Petunjuk ==========
    ws_info = wb.add_worksheet("Petunjuk")
    ws_info.set_column(0, 0, 120)
    
    instructions = [
        "=== PETUNJUK PENGGUNAAN ===",
        "",
        "File ini digunakan untuk upload Kinerja Penjualan bulanan.",
        "",
        "SHEET 1: PENJUALAN",
        "- Isi data penjualan per salesman untuk periode tertentu",
        "- Kolom wajib: employee_id, nama, cabang, periode, penjualan",
        "- Periode format: YYYY-MM (contoh: 2026-09)",
        "- Penjualan dalam angka tanpa koma desimal (contoh: 45000000 bukan 45.000.000)",
        "",
        "SHEET 2: TARGET",
        "- Isi target untuk 3 tingkat: Perusahaan, Cabang, dan Salesman",
        "- Level Perusahaan: kosongkan cabang dan employee_id",
        "- Level Cabang: isi cabang, kosongkan employee_id",
        "- Level Salesman: isi cabang dan employee_id",
        "- Total target salesman di satu cabang bisa berbeda dengan target cabang (tidak harus sama)",
        "",
        "ATURAN KELAYAKAN PERFORMANCE:",
        "- Salesman dinyatakan LAYAK bila penjualan >= target pribadi",
        "- Kekurangan salesman A tidak ditutup oleh kelebihan salesman B",
        "- Untuk cabang: total penjualan semua salesman bisa saling menutupi",
        "",
        "CONTOH:",
        "- Target Salesman A: 40 juta, Penjualan: 45 juta → LAYAK (110%)",
        "- Target Salesman B: 35 juta, Penjualan: 30 juta → TIDAK LAYAK (86%)",
        "- Target Cabang: 200 juta, Total penjualan: 210 juta → Cabang TERCAPAI",
        "",
        "PROSES SETELAH UPLOAD:",
        "1. Sistem otomatis menghitung kelayakan per salesman",
        "2. Status assessment dibuat dengan rekomendasi (Layak/Tidak Layak)",
        "3. Penyetuju Sales/Marketing review dan approve/reject",
        "4. HR lihat hasil akhir untuk hitung komponen performance di gaji",
        "",
        "UPLOAD FILE INI DI:",
        "Menu: Kinerja Penjualan > Upload File",
        "Akses: Sales/Marketing only",
    ]
    
    for i, line in enumerate(instructions):
        if line.startswith("==="):
            ws_info.write(i, 0, line, header)
        else:
            ws_info.write(i, 0, line)
    
    wb.close()
    buf.seek(0)
    return buf


if __name__ == "__main__":
    # Test generate template
    with open("Template_Kinerja_Penjualan.xlsx", "wb") as f:
        f.write(create_sales_template().getvalue())
    print("Template created: Template_Kinerja_Penjualan.xlsx")
