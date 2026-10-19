from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-ganti-di-produksi"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"  # "production" mengaktifkan pemeriksaan keamanan saat startup
    database_url: str = "sqlite:///./dev.db"
    max_upload_mb: int = 50
    storage_dir: Path = Path("storage/uploads")
    cors_origins: str = "http://localhost:3000"

    # Autentikasi
    jwt_secret: str = DEV_JWT_SECRET
    jwt_expire_minutes: int = 480  # satu hari kerja
    cookie_name: str = "presensi_session"
    cookie_secure: bool = False  # WAJIB True di produksi (HTTPS)
    cookie_samesite: str = "lax"  # "lax" cukup selama frontend dan API satu site (mis. app.contoh.id dan api.contoh.id)
    login_max_attempts: int = 5  # gagal berturut-turut sebelum dikunci sementara
    login_lock_minutes: int = 15

    # Akun admin awal, dibuat saat startup bila belum ada (lihat main.py).
    admin_email: str | None = None
    admin_password: str | None = None
    admin_name: str = "HR Admin"

    # Nama departemen pada DATA karyawan (kolom departemen/Organization di file upload) yang termasuk akun
    # departemen Sales/Marketing. Akun Sales/Marketing hanya melihat karyawan dengan nama-nama ini.
    # Sesuaikan dengan isi file sumber, dipisah koma, huruf besar-kecil tidak berpengaruh.
    # Hari terakhir minggu kerja (1=Senin ... 7=Minggu). 6 = Senin-Sabtu (enam hari kerja, Minggu libur).
    # Ubah ke 5 bila perusahaan beralih ke lima hari kerja (Senin-Jumat).
    last_workday: int = 6

    sales_org_names: str = "Sales,Marketing,Sales/Marketing"

    @property
    def sales_orgs(self) -> tuple[str, ...]:
        return tuple(o.strip().lower() for o in self.sales_org_names.split(",") if o.strip())

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


settings = Settings()
