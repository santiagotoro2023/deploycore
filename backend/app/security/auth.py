import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import get_settings

_hasher = PasswordHasher()

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def create_access_token(user_id: uuid.UUID, session_id: str) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": str(user_id), "sid": session_id, "exp": expires_at}
    return jwt.encode(payload, settings.app_secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    return jwt.decode(token, settings.app_secret_key, algorithms=[JWT_ALGORITHM])


def validate_password_strength(password: str) -> None:
    """Raises ValueError (turned into a 422 by Pydantic) for weak passwords.
    Deliberately simple: minimum length plus a letter and a digit, not a
    full entropy/dictionary check."""
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    if not any(c.isalpha() for c in password):
        raise ValueError("password must contain at least one letter")
    if not any(c.isdigit() for c in password):
        raise ValueError("password must contain at least one digit")
