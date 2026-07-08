import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
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

AVATAR_MAX_BYTES = 2 * 1024 * 1024
AVATAR_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _avatars_dir() -> Path:
    path = Path(get_settings().iso_storage_path) / "avatars"
    path.mkdir(parents=True, exist_ok=True)
    return path


async def _org_roles_for(db: AsyncSession, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Role]]:
    result = await db.execute(select(UserOrgRole).where(UserOrgRole.user_id.in_(user_ids)))
    by_user: dict[uuid.UUID, dict[str, Role]] = {}
    for row in result.scalars().all():
        by_user.setdefault(row.user_id, {})[str(row.org_id)] = row.role
    return by_user


def user_has_avatar(user: User) -> bool:
    return bool(user.avatar_filename) and (_avatars_dir() / user.avatar_filename).exists()


def _to_read(user: User, org_roles: dict[str, Role]) -> UserRead:
    read = UserRead.model_validate(user)
    read.org_roles = org_roles
    read.has_avatar = user_has_avatar(user)
    return read


@router.get("", response_model=list[UserRead], dependencies=[_admin_global])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[UserRead]:
    result = await db.execute(select(User))
    users = list(result.scalars().all())
    org_roles = await _org_roles_for(db, [u.id for u in users])
    return [_to_read(u, org_roles.get(u.id, {})) for u in users]


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED, dependencies=[_admin_global])
async def create_user(
    body: UserCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
) -> UserRead:
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
    return _to_read(user, {})


@router.get("/{user_id}", response_model=UserRead, dependencies=[_admin_global])
async def get_user(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> UserRead:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    org_roles = await _org_roles_for(db, [user_id])
    return _to_read(user, org_roles.get(user_id, {}))


@router.patch("/{user_id}", response_model=UserRead, dependencies=[_admin_global])
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserRead:
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
    org_roles = await _org_roles_for(db, [user_id])
    return _to_read(user, org_roles.get(user_id, {}))


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


@router.put("/me/avatar")
async def set_my_avatar(file: UploadFile, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    ext = Path(file.filename or "").suffix.lower()
    if ext not in AVATAR_CONTENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "avatar must be a PNG or JPEG file")
    content = await file.read()
    if len(content) > AVATAR_MAX_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "avatar must be under 2 MB")

    avatars_dir = _avatars_dir()
    for existing in avatars_dir.glob(f"{current_user.id}.*"):
        existing.unlink()
    filename = f"{current_user.id}{ext}"
    (avatars_dir / filename).write_bytes(content)

    current_user.avatar_filename = filename
    audit.record(db, action="user.avatar_set", target_type="user", user_id=current_user.id, target_id=current_user.id)
    await db.commit()
    return {"has_avatar": True}


@router.delete("/me/avatar", status_code=status.HTTP_204_NO_CONTENT)
async def remove_my_avatar(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> None:
    for existing in _avatars_dir().glob(f"{current_user.id}.*"):
        existing.unlink()
    current_user.avatar_filename = None
    audit.record(db, action="user.avatar_remove", target_type="user", user_id=current_user.id, target_id=current_user.id)
    await db.commit()


@router.get("/{user_id}/avatar")
async def get_user_avatar(user_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> FileResponse:
    user = await db.get(User, user_id)
    if user is None or not user.avatar_filename:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no avatar set")
    path = _avatars_dir() / user.avatar_filename
    if not path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no avatar set")
    content_type = AVATAR_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=content_type)


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
