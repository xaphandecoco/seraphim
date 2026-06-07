import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from jwt.exceptions import ExpiredSignatureError, InvalidTokenError, PyJWTError


def hash_password(password: str) -> str:
    """Hash a password using bcrypt directly (passlib 1.7.4 incompatible with bcrypt 5.0+)."""
    pw_bytes = password.encode("utf-8")
    # bcrypt has a 72-byte limit
    if len(pw_bytes) > 72:
        pw_bytes = pw_bytes[:72]
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash."""
    pw_bytes = plain_password.encode("utf-8")
    hash_bytes = hashed_password.encode("utf-8")
    if len(pw_bytes) > 72:
        pw_bytes = pw_bytes[:72]
    return bcrypt.checkpw(pw_bytes, hash_bytes)


def create_access_token(data: dict, secret: str, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=15)
    )
    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})
    encoded_jwt = jwt.encode(to_encode, secret, algorithm="HS256")
    return encoded_jwt


def create_refresh_token(data: dict, secret: str, expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=7))
    to_encode = data.copy()
    to_encode.update({
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "type": "refresh",
        "jti": secrets.token_urlsafe(16),  # S6: unique token ID for denylist
    })
    return jwt.encode(to_encode, secret, algorithm="HS256")


def verify_token(token: str, secret: str, *, require_type: Optional[str] = None) -> Optional[dict]:
    """Decode and validate a JWT.

    Args:
        token: Raw JWT string.
        secret: HMAC secret.
        require_type: If provided, return None when ``payload.get("type") == require_type``.
                      Use ``require_type="refresh"`` on Bearer/query-param paths to block
                      refresh tokens from being replayed against the API surface (S1/Design B).
                      Access tokens carry no ``type`` claim, so they are unaffected.
    """
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if require_type is not None and payload.get("type") == require_type:
            return None
        return payload
    except ExpiredSignatureError:
        return None
    except (InvalidTokenError, PyJWTError):
        return None


def generate_reset_token() -> str:
    return secrets.token_urlsafe(32)


def check_allowed_domain(email: str, domain: str) -> bool:
    return email.lower().endswith(f"@{domain.lower()}")
