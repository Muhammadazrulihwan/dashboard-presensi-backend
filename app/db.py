from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

_url = settings.database_url
if _url.startswith("sqlite"):
    _args = {"check_same_thread": False}
elif _url.startswith("mysql"):
    # created_at memakai CURRENT_TIMESTAMP milik server. Tanpa ini, MySQL menyimpan jam lokal server (WIB),
    # padahal aplikasi menganggap waktu tanpa zona sebagai UTC, sehingga tampil lebih cepat 7 jam di browser.
    # Dengan sesi berzona UTC, nilai yang tersimpan selalu UTC dan tampilan mengikuti zona waktu browser.
    _args = {"init_command": "SET time_zone = '+00:00'"}
else:
    _args = {}
engine = create_engine(_url, connect_args=_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
