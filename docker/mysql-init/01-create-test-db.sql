-- Dijalankan otomatis oleh container MySQL saat volume pertama kali dibuat.
-- Membuat database uji terpisah agar `pytest` dengan TEST_DATABASE_URL tidak menyentuh data dev.
CREATE DATABASE IF NOT EXISTS `presensi_test` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
GRANT ALL PRIVILEGES ON `presensi_test`.* TO 'presensi_app'@'%';
FLUSH PRIVILEGES;
