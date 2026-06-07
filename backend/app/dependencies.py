from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import dynamic_settings
from app.utils.auth import verify_token

security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> dict:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    secret = dynamic_settings.get_jwt_secret()
    # S1/B-1 Critical: Bearer-only path must reject refresh tokens (type="refresh")
    payload = verify_token(credentials.credentials, secret, reject_type="refresh")
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not all(k in payload for k in ("sub", "email", "role")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return {
        "sub": payload["sub"],
        "email": payload["email"],
        "name": payload.get("name"),
        "role": payload["role"],
    }


async def require_admin(
    current_user: dict = Depends(get_current_user),
) -> dict:
    if current_user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def require_volunteer(
    current_user: dict = Depends(get_current_user),
) -> dict:
    if current_user["role"] not in ("admin", "volunteer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Volunteer access required",
        )
    return current_user


async def check_setup_complete(request: Request):
    """Middleware dependency: return 503 if setup is not complete."""
    from app.config import dynamic_settings

    if not dynamic_settings.is_setup_complete():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Setup required. Complete initial configuration at /setup",
        )
