"""Hash password (scrypt, hanya pustaka standar) dan token JWT."""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

_N, _R, _P = 2**14, 8, 1
_ALG = "HS256"


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(_scrypt(password, salt, _N, _R, _P))}"


def verify_password(password: str, stored: str) -> bool:
    """False untuk hash tidak valid, termasuk placeholder "!" milik user dummy lama."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = _scrypt(password, base64.b64decode(salt), int(n), int(r), int(p))
        return hmac.compare_digest(calc, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Hash palsu untuk menyamakan waktu respons saat email tidak ditemukan (mencegah user enumeration).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def burn_time() -> None:
    verify_password("x", _DUMMY_HASH)


def create_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=settings.jwt_expire_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALG)


def decode_token(token: str) -> int | None:
    """ID user dari token yang valid dan belum kedaluwarsa, selain itu None."""
    try:
        data = jwt.decode(token, settings.jwt_secret, algorithms=[_ALG], options={"require": ["exp", "sub"]})
        return int(data["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        return None
