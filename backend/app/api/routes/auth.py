from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import User, UserOrgRole
from app.redis import get_redis
from app.schemas.auth import LoginRequest, MeResponse, TokenResponse
from app.schemas.user import UserRead
from app.security.auth import create_access_token, verify_password
from app.security.rbac import get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])

RATE_LIMIT_ATTEMPTS = 10
RATE_LIMIT_WINDOW_SECONDS = 300


async def _check_rate_limit(redis: Redis, key: str) -> None:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    if count > RATE_LIMIT_ATTEMPTS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many login attempts")


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    await _check_rate_limit(redis, f"ratelimit:login:ip:{client_ip}")
    await _check_rate_limit(redis, f"ratelimit:login:email:{body.email}")

    result = await db.execute(select(User).where(User.email == body.email))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")

    return TokenResponse(access_token=create_access_token(user.id))


@router.get("/me", response_model=MeResponse)
async def me(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> MeResponse:
    result = await db.execute(select(UserOrgRole).where(UserOrgRole.user_id == current_user.id))
    org_roles = {str(row.org_id): row.role for row in result.scalars().all()}
    return MeResponse(user=UserRead.model_validate(current_user), org_roles=org_roles)
