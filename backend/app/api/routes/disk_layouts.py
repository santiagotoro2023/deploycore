import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.disk_layout import DiskLayout
from app.models.user import Role, User
from app.schemas.disk_layout import DiskLayoutCreate, DiskLayoutRead, DiskLayoutUpdate
from app.security.rbac import get_current_user, require_role
from app.services import audit

router = APIRouter(tags=["disk-layouts"])


@router.get(
    "/api/organizations/{org_id}/disk-layouts",
    response_model=list[DiskLayoutRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_disk_layouts(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[DiskLayout]:
    result = await db.execute(
        select(DiskLayout).where(or_(DiskLayout.org_id == org_id, DiskLayout.org_id.is_(None)))
    )
    return list(result.scalars().all())


@router.post(
    "/api/organizations/{org_id}/disk-layouts",
    response_model=DiskLayoutRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def create_disk_layout(
    org_id: uuid.UUID,
    body: DiskLayoutCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DiskLayout:
    layout = DiskLayout(
        org_id=org_id,
        name=body.name,
        layout_json=body.layout.model_dump(),
        post_install_scripts=[s.model_dump() for s in body.post_install_scripts],
    )
    db.add(layout)
    await db.flush()
    audit.record(
        db, action="disk_layout.create", target_type="disk_layout", org_id=org_id,
        user_id=current_user.id, target_id=layout.id, detail={"name": layout.name},
    )
    await db.commit()
    await db.refresh(layout)
    return layout


async def _get_org_owned_layout(db: AsyncSession, org_id: uuid.UUID, layout_id: uuid.UUID) -> DiskLayout:
    layout = await db.get(DiskLayout, layout_id)
    if layout is None or layout.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "disk layout not found in this organization")
    return layout


@router.patch(
    "/api/organizations/{org_id}/disk-layouts/{layout_id}",
    response_model=DiskLayoutRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def update_disk_layout(
    org_id: uuid.UUID,
    layout_id: uuid.UUID,
    body: DiskLayoutUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DiskLayout:
    layout = await _get_org_owned_layout(db, org_id, layout_id)
    if body.name is not None:
        layout.name = body.name
    if body.layout is not None:
        layout.layout_json = body.layout.model_dump()
    if body.post_install_scripts is not None:
        layout.post_install_scripts = [s.model_dump() for s in body.post_install_scripts]
    audit.record(
        db, action="disk_layout.update", target_type="disk_layout", org_id=org_id,
        user_id=current_user.id, target_id=layout.id,
    )
    await db.commit()
    await db.refresh(layout)
    return layout


@router.delete(
    "/api/organizations/{org_id}/disk-layouts/{layout_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def delete_disk_layout(
    org_id: uuid.UUID,
    layout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    layout = await _get_org_owned_layout(db, org_id, layout_id)
    audit.record(
        db, action="disk_layout.delete", target_type="disk_layout", org_id=org_id,
        user_id=current_user.id, target_id=layout.id, detail={"name": layout.name},
    )
    await db.delete(layout)
    await db.commit()


@router.get(
    "/api/organizations/{org_id}/disk-layouts/{layout_id}/export",
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def export_disk_layout(
    org_id: uuid.UUID,
    layout_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    result = await db.execute(
        select(DiskLayout).where(
            DiskLayout.id == layout_id, or_(DiskLayout.org_id == org_id, DiskLayout.org_id.is_(None))
        )
    )
    layout = result.scalar_one_or_none()
    if layout is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "disk layout not found")
    audit.record(
        db, action="disk_layout.export", target_type="disk_layout", org_id=org_id,
        user_id=current_user.id, target_id=layout.id,
    )
    await db.commit()
    return {"name": layout.name, "layout": layout.layout_json, "post_install_scripts": layout.post_install_scripts}


@router.post(
    "/api/organizations/{org_id}/disk-layouts/import",
    response_model=DiskLayoutRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def import_disk_layout(
    org_id: uuid.UUID,
    body: DiskLayoutCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DiskLayout:
    layout = DiskLayout(
        org_id=org_id,
        name=body.name,
        layout_json=body.layout.model_dump(),
        post_install_scripts=[s.model_dump() for s in body.post_install_scripts],
    )
    db.add(layout)
    await db.flush()
    audit.record(
        db, action="disk_layout.import", target_type="disk_layout", org_id=org_id,
        user_id=current_user.id, target_id=layout.id, detail={"name": layout.name},
    )
    await db.commit()
    await db.refresh(layout)
    return layout


@router.post(
    "/api/disk-layouts/global",
    response_model=DiskLayoutRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def create_global_disk_layout(
    body: DiskLayoutCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DiskLayout:
    layout = DiskLayout(
        org_id=None,
        name=body.name,
        layout_json=body.layout.model_dump(),
        post_install_scripts=[s.model_dump() for s in body.post_install_scripts],
    )
    db.add(layout)
    await db.flush()
    audit.record(
        db, action="disk_layout.create_global", target_type="disk_layout",
        user_id=current_user.id, target_id=layout.id, detail={"name": layout.name},
    )
    await db.commit()
    await db.refresh(layout)
    return layout
