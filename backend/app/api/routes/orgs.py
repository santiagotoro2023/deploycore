import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.org import Organization
from app.models.user import Role, User, UserOrgRole
from app.schemas.org import OrganizationCreate, OrganizationRead, OrganizationUpdate
from app.security.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


@router.get("", response_model=list[OrganizationRead])
async def list_organizations(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Organization]:
    if current_user.global_role != Role.NONE:
        result = await db.execute(select(Organization))
        return list(result.scalars().all())
    result = await db.execute(
        select(Organization)
        .join(UserOrgRole, UserOrgRole.org_id == Organization.id)
        .where(UserOrgRole.user_id == current_user.id)
    )
    return list(result.scalars().all())


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
async def create_organization(
    body: OrganizationCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(Role.ADMIN, org_scoped=False)),
) -> Organization:
    org = Organization(**body.model_dump())
    db.add(org)
    await db.commit()
    await db.refresh(org)
    return org


@router.get("/{org_id}", response_model=OrganizationRead)
async def get_organization(
    org_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(Role.READONLY)),
) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
    return org


@router.patch("/{org_id}", response_model=OrganizationRead)
async def update_organization(
    org_id: uuid.UUID,
    body: OrganizationUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role(Role.ADMIN)),
) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "organization not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(org, field, value)
    await db.commit()
    await db.refresh(org)
    return org
