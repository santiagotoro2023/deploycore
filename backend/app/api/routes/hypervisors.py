import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.jobs import get_arq_pool
from app.models.hypervisor import HypervisorHost
from app.models.user import Role
from app.schemas.hypervisor import HypervisorHostCreate, HypervisorHostRead, HypervisorHostUpdate
from app.security.rbac import require_role

router = APIRouter(prefix="/api/organizations/{org_id}/hypervisors", tags=["hypervisors"])

TEST_CONNECTION_TIMEOUT_SECONDS = 20


@router.get("", response_model=list[HypervisorHostRead], dependencies=[Depends(require_role(Role.READONLY))])
async def list_hypervisors(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[HypervisorHost]:
    result = await db.execute(select(HypervisorHost).where(HypervisorHost.org_id == org_id))
    return list(result.scalars().all())


@router.post(
    "",
    response_model=HypervisorHostRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def create_hypervisor(
    org_id: uuid.UUID, body: HypervisorHostCreate, db: AsyncSession = Depends(get_db)
) -> HypervisorHost:
    host = HypervisorHost(
        org_id=org_id,
        name=body.name,
        type=body.type,
        api_endpoint=body.api_endpoint,
        username=body.username,
        tls_verify=body.tls_verify,
        default_datastore=body.default_datastore,
        default_network=body.default_network,
    )
    host.credential = body.credential
    db.add(host)
    await db.commit()
    await db.refresh(host)
    return host


async def _get_host_or_404(db: AsyncSession, org_id: uuid.UUID, host_id: uuid.UUID) -> HypervisorHost:
    result = await db.execute(
        select(HypervisorHost).where(HypervisorHost.id == host_id, HypervisorHost.org_id == org_id)
    )
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "hypervisor host not found")
    return host


@router.get(
    "/{host_id}", response_model=HypervisorHostRead, dependencies=[Depends(require_role(Role.READONLY))]
)
async def get_hypervisor(
    org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> HypervisorHost:
    return await _get_host_or_404(db, org_id, host_id)


@router.patch(
    "/{host_id}", response_model=HypervisorHostRead, dependencies=[Depends(require_role(Role.ADMIN))]
)
async def update_hypervisor(
    org_id: uuid.UUID, host_id: uuid.UUID, body: HypervisorHostUpdate, db: AsyncSession = Depends(get_db)
) -> HypervisorHost:
    host = await _get_host_or_404(db, org_id, host_id)
    updates = body.model_dump(exclude_unset=True, exclude={"credential"})
    for field, value in updates.items():
        setattr(host, field, value)
    if body.credential:
        host.credential = body.credential
    await db.commit()
    await db.refresh(host)
    return host


@router.delete("/{host_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_role(Role.ADMIN))])
async def delete_hypervisor(org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> None:
    host = await _get_host_or_404(db, org_id, host_id)
    await db.delete(host)
    await db.commit()


@router.post(
    "/{host_id}/test-connection",
    response_model=HypervisorHostRead,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def test_connection(
    org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> HypervisorHost:
    host = await _get_host_or_404(db, org_id, host_id)
    pool = await get_arq_pool()
    job = await pool.enqueue_job("test_hypervisor_connection", str(host_id))
    await job.result(timeout=TEST_CONNECTION_TIMEOUT_SECONDS)
    await db.refresh(host)
    return host
