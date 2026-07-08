import asyncio
import json
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal, get_db
from app.hypervisors import get_driver
from app.jobs import get_arq_pool
from app.models.deployment import Deployment, DeploymentLogLine, DeploymentState, DeploymentStateTransition
from app.models.hypervisor import HypervisorHost
from app.models.user import Role, User
from app.schemas.deployment import (
    DeploymentCreate,
    DeploymentLogLineRead,
    DeploymentRead,
    DeploymentStateTransitionRead,
    PowerAction,
    PowerStateRead,
)
from app.security.rbac import get_current_user, require_role
from app.services import audit
from app.services.deployment_service import InvalidTransition, log, retry_deployment

router = APIRouter(tags=["deployments"])

EVENTS_POLL_INTERVAL_SECONDS = 1


@router.get(
    "/api/organizations/{org_id}/deployments",
    response_model=list[DeploymentRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_deployments(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[Deployment]:
    result = await db.execute(
        select(Deployment).where(Deployment.org_id == org_id).order_by(Deployment.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/api/organizations/{org_id}/deployments",
    response_model=DeploymentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def create_deployment(
    org_id: uuid.UUID,
    body: DeploymentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Deployment:
    deployment = Deployment(
        org_id=org_id,
        template_id=body.template_id,
        hypervisor_host_id=body.hypervisor_host_id,
        hostname=body.hostname,
        ip_mode=body.ip_mode,
        static_ip=body.static_ip,
        static_netmask=body.static_netmask,
        static_gateway=body.static_gateway,
        static_dns=body.static_dns,
        callback_token=secrets.token_urlsafe(32),
        created_by_user_id=current_user.id,
    )
    audit.record(
        db,
        action="deployment.create",
        target_type="deployment",
        org_id=org_id,
        user_id=current_user.id,
        target_id=deployment.id,
        detail={"hostname": body.hostname},
    )
    db.add(deployment)
    await db.commit()
    await db.refresh(deployment)

    pool = await get_arq_pool()
    await pool.enqueue_job("run_deployment", str(deployment.id))
    return deployment


async def _get_org_deployment(db: AsyncSession, org_id: uuid.UUID, deployment_id: uuid.UUID) -> Deployment:
    result = await db.execute(
        select(Deployment).where(Deployment.id == deployment_id, Deployment.org_id == org_id)
    )
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "deployment not found in this organization")
    return deployment


@router.get(
    "/api/organizations/{org_id}/deployments/{deployment_id}",
    response_model=DeploymentRead,
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def get_deployment(
    org_id: uuid.UUID, deployment_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Deployment:
    return await _get_org_deployment(db, org_id, deployment_id)


@router.get(
    "/api/organizations/{org_id}/deployments/{deployment_id}/history",
    response_model=list[DeploymentStateTransitionRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def get_deployment_history(
    org_id: uuid.UUID, deployment_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[DeploymentStateTransition]:
    await _get_org_deployment(db, org_id, deployment_id)
    result = await db.execute(
        select(DeploymentStateTransition)
        .where(DeploymentStateTransition.deployment_id == deployment_id)
        .order_by(DeploymentStateTransition.occurred_at)
    )
    return list(result.scalars().all())


@router.get(
    "/api/organizations/{org_id}/deployments/{deployment_id}/logs",
    response_model=list[DeploymentLogLineRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def get_deployment_logs(
    org_id: uuid.UUID, deployment_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[DeploymentLogLine]:
    await _get_org_deployment(db, org_id, deployment_id)
    result = await db.execute(
        select(DeploymentLogLine).where(DeploymentLogLine.deployment_id == deployment_id).order_by(DeploymentLogLine.ts)
    )
    return list(result.scalars().all())


@router.post(
    "/api/organizations/{org_id}/deployments/{deployment_id}/retry",
    response_model=DeploymentRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def retry(
    org_id: uuid.UUID, deployment_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Deployment:
    deployment = await _get_org_deployment(db, org_id, deployment_id)
    try:
        await retry_deployment(db, deployment)
    except InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    pool = await get_arq_pool()
    await pool.enqueue_job("run_deployment", str(deployment.id))
    await db.refresh(deployment)
    return deployment


async def _driver_for(db: AsyncSession, deployment: Deployment):
    host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
    return get_driver(host)


@router.get(
    "/api/organizations/{org_id}/deployments/{deployment_id}/power",
    response_model=PowerStateRead,
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def get_power_state(
    org_id: uuid.UUID, deployment_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> PowerStateRead:
    deployment = await _get_org_deployment(db, org_id, deployment_id)
    if deployment.vm_moref is None:
        return PowerStateRead(power_state=None)
    driver = await _driver_for(db, deployment)
    state = await driver.get_power_state(deployment.vm_moref)
    return PowerStateRead(power_state=state.value)


@router.post(
    "/api/organizations/{org_id}/deployments/{deployment_id}/power/on",
    response_model=PowerStateRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def power_on(
    org_id: uuid.UUID,
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PowerStateRead:
    deployment = await _get_org_deployment(db, org_id, deployment_id)
    if deployment.vm_moref is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no VM exists for this deployment")
    driver = await _driver_for(db, deployment)
    await driver.power_on(deployment.vm_moref)
    await log(db, deployment, "lifecycle", "VM powered on")
    audit.record(
        db, action="deployment.power_on", target_type="deployment",
        org_id=org_id, user_id=current_user.id, target_id=deployment.id,
    )
    await db.commit()
    state = await driver.get_power_state(deployment.vm_moref)
    return PowerStateRead(power_state=state.value)


@router.post(
    "/api/organizations/{org_id}/deployments/{deployment_id}/power/off",
    response_model=PowerStateRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def power_off(
    org_id: uuid.UUID,
    deployment_id: uuid.UUID,
    body: PowerAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PowerStateRead:
    deployment = await _get_org_deployment(db, org_id, deployment_id)
    if deployment.vm_moref is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no VM exists for this deployment")
    driver = await _driver_for(db, deployment)
    await driver.power_off(deployment.vm_moref, hard=body.hard)
    await log(db, deployment, "lifecycle", f"VM powered off ({'hard' if body.hard else 'graceful'})")
    audit.record(
        db, action="deployment.power_off", target_type="deployment",
        org_id=org_id, user_id=current_user.id, target_id=deployment.id, detail={"hard": body.hard},
    )
    await db.commit()
    state = await driver.get_power_state(deployment.vm_moref)
    return PowerStateRead(power_state=state.value)


@router.delete(
    "/api/organizations/{org_id}/deployments/{deployment_id}/vm",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def delete_vm(
    org_id: uuid.UUID,
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deployment = await _get_org_deployment(db, org_id, deployment_id)
    if deployment.vm_moref is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "no VM exists for this deployment")
    driver = await _driver_for(db, deployment)
    await driver.delete_vm(deployment.vm_moref)
    await log(db, deployment, "lifecycle", "VM deleted")
    audit.record(
        db, action="deployment.delete_vm", target_type="deployment",
        org_id=org_id, user_id=current_user.id, target_id=deployment.id,
    )
    deployment.vm_moref = None
    await db.commit()


async def _event_stream(deployment_id: uuid.UUID, request: Request):
    """Owns its own DB session rather than reusing the request's — a
    `Depends(get_db)` session is torn down once the endpoint function
    returns, before a StreamingResponse body finishes sending."""
    last_log_ts = None
    last_transition_ts = None
    async with SessionLocal() as db:
        while True:
            if await request.is_disconnected():
                break

            log_stmt = select(DeploymentLogLine).where(DeploymentLogLine.deployment_id == deployment_id)
            if last_log_ts is not None:
                log_stmt = log_stmt.where(DeploymentLogLine.ts > last_log_ts)
            log_stmt = log_stmt.order_by(DeploymentLogLine.ts)
            for line in (await db.execute(log_stmt)).scalars().all():
                last_log_ts = line.ts
                yield f"event: log\ndata: {json.dumps({'ts': line.ts.isoformat(), 'stage': line.stage, 'level': line.level.value, 'message': line.message})}\n\n"

            transition_stmt = select(DeploymentStateTransition).where(
                DeploymentStateTransition.deployment_id == deployment_id
            )
            if last_transition_ts is not None:
                transition_stmt = transition_stmt.where(DeploymentStateTransition.occurred_at > last_transition_ts)
            transition_stmt = transition_stmt.order_by(DeploymentStateTransition.occurred_at)
            terminal = False
            for t in (await db.execute(transition_stmt)).scalars().all():
                last_transition_ts = t.occurred_at
                yield f"event: transition\ndata: {json.dumps({'from_state': t.from_state, 'to_state': t.to_state, 'occurred_at': t.occurred_at.isoformat(), 'detail': t.detail})}\n\n"
                if t.to_state in (DeploymentState.COMPLETED.value, DeploymentState.FAILED.value):
                    terminal = True

            if terminal:
                break
            await asyncio.sleep(EVENTS_POLL_INTERVAL_SECONDS)


@router.get(
    "/api/organizations/{org_id}/deployments/{deployment_id}/events",
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def deployment_events(
    org_id: uuid.UUID, deployment_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)
) -> StreamingResponse:
    await _get_org_deployment(db, org_id, deployment_id)
    return StreamingResponse(_event_stream(deployment_id, request), media_type="text/event-stream")
