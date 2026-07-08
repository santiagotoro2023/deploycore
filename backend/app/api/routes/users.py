import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.user import Role, User, UserOrgRole
from app.redis import get_redis
from app.schemas.user import OrgRoleAssign, UserCreate, UserRead, UserUpdate
from app.security.auth import hash_password
from app.security.rbac import get_current_user, require_role
from app.security.sessions import revoke_all_sessions
from app.services import audit

router = APIRouter(prefix="/api/users", tags=["users"])

_admin_global = Depends(require_role(Role.ADMIN, org_scoped=False))


@router.get("", response_model=list[UserRead], dependencies=[_admin_global])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[User]:
    result = await db.execute(select(User))
    return list(result.scalars().all())


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED, dependencies=[_admin_global])
async def create_user(
    body: UserCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> User:
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "a user with that username already exists")
    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        global_role=body.global_role,
    )
    db.add(user)
    await db.flush()
    audit.record(
        db, action="user.create", target_type="user", user_id=current_user.id, target_id=user.id,
        detail={"username": body.username},
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/{user_id}", response_model=UserRead, dependencies=[_admin_global])
async def get_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return user


@router.patch("/{user_id}", response_model=UserRead, dependencies=[_admin_global])
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    updates = body.model_dump(exclude_unset=True, exclude={"password"})
    for field, value in updates.items():
        setattr(user, field, value)
    if body.password:
        user.password_hash = hash_password(body.password)
    audit.record(
        db, action="user.update", target_type="user", user_id=current_user.id, target_id=user.id,
        detail={"fields": list(updates.keys()) + (["password"] if body.password else [])},
    )
    await db.commit()
    await db.refresh(user)
    return user


@router.post("/{user_id}/org-roles", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_admin_global])
async def assign_org_role(
    user_id: uuid.UUID,
    body: OrgRoleAssign,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    existing = await db.get(UserOrgRole, (user_id, body.org_id))
    if existing is not None:
        existing.role = body.role
    else:
        db.add(UserOrgRole(user_id=user_id, org_id=body.org_id, role=body.role))
    audit.record(
        db, action="user.org_role_assign", target_type="user", org_id=body.org_id,
        user_id=current_user.id, target_id=user_id, detail={"role": body.role.value},
    )
    await db.commit()


@router.delete("/{user_id}/org-roles/{org_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_admin_global])
async def remove_org_role(
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    existing = await db.get(UserOrgRole, (user_id, org_id))
    if existing is not None:
        await db.delete(existing)
        audit.record(
            db, action="user.org_role_remove", target_type="user", org_id=org_id,
            user_id=current_user.id, target_id=user_id,
        )
        await db.commit()


@router.post("/{user_id}/force-logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[_admin_global])
async def force_logout(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
) -> None:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    await revoke_all_sessions(redis, user_id)
    audit.record(
        db, action="auth.force_logout", target_type="user", user_id=current_user.id, target_id=user_id,
    )
    await db.commit()
