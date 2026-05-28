import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import dynamic_settings, legacy_settings
from app.rate_limit import limiter
from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models import User
from app.schemas import (
    AddVolunteerRequest,
    LoginRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    TokenResponse,
    UserResponse,
)
from app.utils.auth import (
    check_allowed_domain,
    create_access_token,
    create_refresh_token,
    generate_reset_token,
    hash_password,
    verify_password,
    verify_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])

state_serializer = URLSafeTimedSerializer(
    dynamic_settings.get_jwt_secret() or legacy_settings.SECRET_KEY, salt="oauth-state"
)


def generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    code_verifier = secrets.token_urlsafe(64)[:128]  # 43–128 chars per RFC 7636
    code_challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
        .rstrip(b"=")
        .decode("ascii")
    )
    return code_verifier, code_challenge


@router.post("/login", response_model=TokenResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Local email/password login."""
    result = await db.execute(select(User).where(User.email == req.email.lower()))
    user = result.scalar_one_or_none()

    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deactivated",
        )

    secret = dynamic_settings.get_jwt_secret() or legacy_settings.SECRET_KEY
    access_token = create_access_token(
        {"sub": str(user.id), "email": user.email, "name": getattr(user, "name", None) or user.email, "role": user.role},
        secret=secret,
        expires_delta=timedelta(minutes=dynamic_settings.get_access_token_expire_minutes()),
    )
    refresh_token = create_refresh_token(
        {"sub": str(user.id), "email": user.email, "name": getattr(user, "name", None) or user.email, "role": user.role},
        secret=secret,
        expires_delta=timedelta(days=dynamic_settings.get_refresh_token_expire_days()),
    )

    response = Response(
        content=TokenResponse(access_token=access_token).model_dump_json(),
        media_type="application/json",
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=legacy_settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=dynamic_settings.get_refresh_token_expire_days() * 86400,
    )
    return response


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(key="refresh_token")
    return {"message": "Logged out"}


@router.get("/me", response_model=UserResponse)
async def me(current_user: dict = Depends(get_current_user)):
    return UserResponse(
        id=int(current_user["sub"]),
        email=current_user["email"],
        name=current_user.get("name"),
        role=current_user["role"],
        is_active=True,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: Request):
    refresh_tok = request.cookies.get("refresh_token")
    if not refresh_tok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing refresh token",
        )

    secret = dynamic_settings.get_jwt_secret() or legacy_settings.SECRET_KEY
    payload = verify_token(refresh_tok, secret)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    access_token = create_access_token(
        {
            "sub": payload["sub"],
            "email": payload["email"],
            "name": payload.get("name"),
            "role": payload["role"],
        },
        secret=secret,
        expires_delta=timedelta(minutes=dynamic_settings.get_access_token_expire_minutes()),
    )
    return TokenResponse(access_token=access_token)


@router.post("/reset-password", response_model=dict)
async def request_password_reset(
    req: PasswordResetRequest,
    current_user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin requests password reset for a volunteer. Generates a reset link."""
    result = await db.execute(select(User).where(User.email == req.email.lower()))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    token = generate_reset_token()
    from app.utils.auth import hash_password as _hash
    user.password_reset_token = _hash(token)  # Store hash, not plaintext
    user.password_reset_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    await db.commit()

    return {
        "message": "Reset link generated",
        "reset_link": f"/reset-password?token={token}&email={req.email}",
        "token": token,  # Admin shares this with volunteer
    }


@router.post("/reset-password/confirm", response_model=dict)
@limiter.limit("3/minute")
async def confirm_password_reset(
    request: Request,
    req: PasswordResetConfirm,
    db: AsyncSession = Depends(get_db),
):
    """Volunteer resets password using admin-provided token."""
    result = await db.execute(select(User).where(User.email == req.email.lower()))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # Verify reset token
    if not user.password_reset_token or not user.password_reset_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active password reset request",
        )
    if datetime.now(timezone.utc) > user.password_reset_expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset token expired",
        )
    if not verify_password(req.token, user.password_reset_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid reset token",
        )

    user.password_hash = hash_password(req.new_password)
    user.password_reset_token = None
    user.password_reset_expires_at = None
    await db.commit()
    return {"message": "Password updated successfully"}


@router.post("/add-volunteer", response_model=UserResponse)
async def add_volunteer(
    req: AddVolunteerRequest,
    current_user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin creates a new volunteer account. No self-registration."""
    existing = await db.execute(select(User).where(User.email == req.email.lower()))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    user = User(
        email=req.email.lower(),
        password_hash=hash_password(req.temporary_password),
        auth_provider="local",
        role=req.role,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    return UserResponse(
        id=user.id,
        email=user.email,
        name=req.name,
        role=user.role,
        is_active=user.is_active,
    )


@router.post("/deactivate/{user_id}", response_model=dict)
async def deactivate_user(
    user_id: int,
    current_user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a user account. Retains leaderboard history."""
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    user.is_active = False
    await db.commit()
    return {"message": f"User {user.email} deactivated"}


# Google OAuth (optional, env-gated)
@router.get("/google")
async def google_login(request: Request, response: Response):
    if not dynamic_settings.get_enable_google_oauth():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth not configured",
        )

    state = secrets.token_urlsafe(32)
    code_verifier, code_challenge = generate_pkce_pair()
    redirect_uri = f"{str(request.base_url).rstrip('/')}/auth/google/callback"
    params = {
        "client_id": dynamic_settings.get_google_client_id(),
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)
    response = RedirectResponse(url=auth_url)
    response.set_cookie(
        key="oauth_state",
        value=state_serializer.dumps(state),
        max_age=600,
        httponly=True,
        secure=legacy_settings.ENVIRONMENT == "production",
        samesite="lax",
    )
    response.set_cookie(
        key="oauth_verifier",
        value=code_verifier,
        max_age=600,
        httponly=True,
        secure=legacy_settings.ENVIRONMENT == "production",
        samesite="lax",
    )
    return response


@router.get("/google/callback")
@limiter.limit("10/minute")
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"OAuth error: {error}")
    if not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing authorization code")

    signed_state = request.cookies.get("oauth_state")
    if not signed_state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing state cookie")

    try:
        expected_state = state_serializer.loads(signed_state, max_age=600)
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid or expired state")
    if state != expected_state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="State mismatch")

    # Retrieve PKCE code_verifier from cookie
    code_verifier = request.cookies.get("oauth_verifier")
    if not code_verifier:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing code verifier")

    redirect_uri = f"{str(request.base_url).rstrip('/')}/auth/google/callback"
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": dynamic_settings.get_google_client_id(),
                "client_secret": dynamic_settings.get_google_client_secret(),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to exchange code for token")
        tokens = token_resp.json()

        userinfo_resp = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if userinfo_resp.status_code != 200:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to fetch user info")
        user_info = userinfo_resp.json()

    email = user_info.get("email", "").lower()
    name = user_info.get("name", "")
    google_id = user_info.get("sub")

    if not email or not google_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Incomplete user info from Google")

    if not check_allowed_domain(email, dynamic_settings.get_allowed_domain()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access restricted to @{dynamic_settings.get_allowed_domain()} domain",
        )

    # Find or create user
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user:
        user = User(email=email, password_hash="", auth_provider="google", role="volunteer", is_active=True)
        db.add(user)
        await db.commit()
        await db.refresh(user)

    secret = dynamic_settings.get_jwt_secret() or legacy_settings.SECRET_KEY
    access_token = create_access_token(
        {"sub": str(user.id), "email": email, "name": name, "role": user.role},
        secret=secret,
    )
    refresh_token = create_refresh_token(
        {"sub": str(user.id), "email": email, "name": name, "role": user.role},
        secret=secret,
    )

    response = RedirectResponse(
        url=f"{legacy_settings.FRONTEND_URL}/auth/callback?token={access_token}"
    )
    response.set_cookie(
        key="refresh_token",
        value=refresh_token,
        httponly=True,
        secure=legacy_settings.ENVIRONMENT == "production",
        samesite="lax",
        max_age=dynamic_settings.get_refresh_token_expire_days() * 86400,
    )
    response.delete_cookie(key="oauth_state")
    response.delete_cookie(key="oauth_verifier")
    return response
