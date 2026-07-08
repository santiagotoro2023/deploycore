import uuid

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import ROLE_ORDER, Role, User, UserOrgRole
from app.redis import get_redis
from app.security.auth import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def _decode_or_401(credentials: HTTPAuthorizationCredentials | None) -> dict:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    try:
        return decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")


async def get_current_session_id(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    return _decode_or_401(credentials)["sid"]


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    payload = _decode_or_401(credentials)
    if not await redis.exists(f"session:{payload['sid']}"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session revoked or expired")
    user = await db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not found or inactive")
    return user


def get_current_org(request: Request) -> uuid.UUID | None:
    """Org scope comes from the `org_id` path or query parameter every
    org-scoped route declares, there is no implicit "current org" state."""
    org_id = request.path_params.get("org_id") or request.query_params.get("org_id")
    return uuid.UUID(org_id) if org_id else None


async def resolve_effective_role(
    db: AsyncSession, user: User, org_id: uuid.UUID | None
) -> Role:
    effective = user.global_role
    if org_id is not None:
        result = await db.execute(
            select(UserOrgRole.role).where(
                UserOrgRole.user_id == user.id, UserOrgRole.org_id == org_id
            )
        )
        org_role = result.scalar_one_or_none()
        if org_role is not None and ROLE_ORDER[org_role] > ROLE_ORDER[effective]:
            effective = org_role
    return effective


def require_role(min_role: Role, org_scoped: bool = True):
    """Dependency factory every route (including GETs) must declare.
    Resolves the caller's effective role for the request's org and 403s
    below `min_role`. `test_rbac.py` introspects the route table to prove
    no mutating route skips this."""

    async def _dep(
        current_user: User = Depends(get_current_user),
        org_id: uuid.UUID | None = Depends(get_current_org),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        effective = await resolve_effective_role(db, current_user, org_id if org_scoped else None)
        if ROLE_ORDER[effective] < ROLE_ORDER[min_role]:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return current_user

    return _dep
