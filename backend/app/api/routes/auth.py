import secrets
import uuid

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.users import user_has_avatar
from app.db import get_db
from app.models.user import User, UserOrgRole
from app.redis import get_redis
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    MeResponse,
    TokenResponse,
    TotpCodeRequest,
    TotpLoginRequest,
    TotpRequiredResponse,
    TotpSetupResponse,
)
from app.schemas.user import UserRead
from app.security.auth import create_access_token, hash_password, verify_password
from app.security.rbac import get_current_session_id, get_current_user
from app.security.sessions import create_session, revoke_all_sessions, revoke_session
from app.services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])

RATE_LIMIT_ATTEMPTS = 10
RATE_LIMIT_WINDOW_SECONDS = 300
TOTP_TICKET_TTL_SECONDS = 300


async def _check_rate_limit(redis: Redis, key: str) -> None:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    if count > RATE_LIMIT_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts")


async def _issue_token(db: AsyncSession, redis: Redis, user: User) -> TokenResponse:
    session_id = await create_session(redis, user.id)
    audit.record(db, action="auth.login", target_type="user", user_id=user.id)
    await db.commit()
    return TokenResponse(access_token=create_access_token(user.id, session_id))


@router.post("/login", response_model=TokenResponse | TotpRequiredResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenResponse | TotpRequiredResponse:
    client_ip = request.client.host if request.client else "unknown"
    await _check_rate_limit(redis, f"ratelimit:login:ip:{client_ip}")
    await _check_rate_limit(redis, f"ratelimit:login:username:{body.username}")

    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        audit.record(db, action="auth.login_failed", target_type="user", detail={"username": body.username})
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid username or password")

    if user.totp_enabled:
        ticket = secrets.token_urlsafe(24)
        await redis.set(f"totp_ticket:{ticket}", str(user.id), ex=TOTP_TICKET_TTL_SECONDS)
        return TotpRequiredResponse(ticket=ticket)

    return await _issue_token(db, redis, user)


@router.post("/login/totp", response_model=TokenResponse)
async def login_totp(
    body: TotpLoginRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenResponse:
    user_id = await redis.get(f"totp_ticket:{body.ticket}")
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "totp ticket expired or invalid")
    user = await db.get(User, uuid.UUID(user_id))
    if user is None or not user.is_active or not user.totp_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid user")
    if not pyotp.TOTP(user.totp_secret).verify(body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid code")
    await redis.delete(f"totp_ticket:{body.ticket}")
    return await _issue_token(db, redis, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current_user: User = Depends(get_current_user),
    session_id: str = Depends(get_current_session_id),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    await revoke_session(redis, session_id)
    audit.record(db, action="auth.logout", target_type="user", user_id=current_user.id)
    await db.commit()


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    await revoke_all_sessions(redis, current_user.id)
    audit.record(db, action="auth.logout_all", target_type="user", user_id=current_user.id)
    await db.commit()


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    """Self-service, requires the current password (unlike an admin's
    PATCH /api/users/{id}, which can set a new one without it - that's a
    deliberately different, higher-trust action). Revokes every session
    for this user afterward, same as logout-all: the request that made
    this call is already authenticated by the time it runs, but nothing
    else should keep working on the old password's say-so - the frontend
    treats this exactly like logout-all, clearing local state and
    redirecting to /login right after."""
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password is incorrect")
    current_user.password_hash = hash_password(body.new_password)
    audit.record(db, action="auth.password_changed", target_type="user", user_id=current_user.id)
    await db.commit()
    await revoke_all_sessions(redis, current_user.id)


@router.post("/2fa/setup", response_model=TotpSetupResponse)
async def setup_totp(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> TotpSetupResponse:
    secret = pyotp.random_base32()
    current_user.totp_secret = secret
    await db.commit()
    otpauth_url = pyotp.TOTP(secret).provisioning_uri(name=current_user.username, issuer_name="DeployCore")
    return TotpSetupResponse(secret=secret, otpauth_url=otpauth_url)


@router.post("/2fa/confirm", status_code=status.HTTP_204_NO_CONTENT)
async def confirm_totp(
    body: TotpCodeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if current_user.totp_secret is None or not pyotp.TOTP(current_user.totp_secret).verify(body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid code")
    current_user.totp_enabled = True
    audit.record(db, action="auth.2fa_enabled", target_type="user", user_id=current_user.id)
    await db.commit()


@router.post("/2fa/disable", status_code=status.HTTP_204_NO_CONTENT)
async def disable_totp(
    body: TotpCodeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if current_user.totp_secret is None or not pyotp.TOTP(current_user.totp_secret).verify(body.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid code")
    current_user.totp_enabled = False
    current_user.totp_secret_encrypted = None
    audit.record(db, action="auth.2fa_disabled", target_type="user", user_id=current_user.id)
    await db.commit()


@router.get("/me", response_model=MeResponse)
async def me(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> MeResponse:
    result = await db.execute(select(UserOrgRole).where(UserOrgRole.user_id == current_user.id))
    org_roles = {str(row.org_id): row.role for row in result.scalars().all()}
    user_read = UserRead.model_validate(current_user)
    user_read.has_avatar = user_has_avatar(current_user)
    return MeResponse(user=user_read, org_roles=org_roles)
