from sqlalchemy.orm import Session

from app.models import AuditLog


def log(db: Session, user_id: int | None, action: str, detail: dict | None = None, ip: str | None = None) -> None:
    """Menambahkan baris audit. Commit dilakukan oleh pemanggil."""
    db.add(AuditLog(user_id=user_id, action=action, detail=detail, ip_address=ip))
