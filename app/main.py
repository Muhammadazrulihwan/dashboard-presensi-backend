from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.db import SessionLocal
from app.routers import admin, archive, auth, dashboard, employees, exports, periods, sales, settings as settings_router, uploads
from app.routers.audit import router as audit_router
from app.services.bootstrap import check_production_settings, ensure_admin, ensure_departments
from app.services.app_settings import ensure_defaults as ensure_app_settings
from app.services.cleanup import purge_stale_uploads
from app.services.login_limiter import purge_expired as purge_expired_login_attempts
from app.services.rules import ensure_default_shift


@asynccontextmanager
async def lifespan(_: FastAPI):
    check_production_settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    settings.archive_dir.mkdir(parents=True, exist_ok=True)
    # Skema dikelola Alembic: jalankan `alembic upgrade head` sebelum memulai server.
    with SessionLocal() as db:
        ensure_departments(db)
        ensure_admin(db)
        ensure_default_shift(db)
        ensure_app_settings(db)
        purge_stale_uploads(db)
        purge_expired_login_attempts(db)
    yield


app = FastAPI(title="Dashboard Presensi API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.include_router(auth.router)
app.include_router(uploads.router)
app.include_router(dashboard.router)
app.include_router(employees.router)
app.include_router(settings_router.router)
app.include_router(exports.router)
app.include_router(admin.router)
app.include_router(audit_router)
app.include_router(sales.router)
app.include_router(archive.router)
app.include_router(periods.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Dashboard Presensi API",
        "docs": "/docs",
        "redoc": "/redoc"
    }
