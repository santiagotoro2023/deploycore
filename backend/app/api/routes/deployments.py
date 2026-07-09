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
from app.models.base import utcnow
from app.models.deployment import (
    Deployment,
    DeploymentHealthCheck,
    DeploymentLogLine,
    DeploymentState,
    DeploymentStateTransition,
    IpMode,
)
from app.models.hypervisor import HypervisorHost
from app.models.user import Role, User
from app.schemas.deployment import (
    BulkDeploymentCreate,
    DeploymentCreate,
    DeploymentHealthCheckRead,
    DeploymentLogLineRead,
    DeploymentRead,
    DeploymentStateTransitionRead,
    PowerAction,
    PowerStateRead,
)
from app.security.rbac import get_current_user, require_role
from app.services import audit, notifications, webhooks
from app.services.deployment_service import InvalidTransition, log, retry_deployment

router = APIRouter(tags=["deployments"])

EVENTS_POLL_INTERVAL_SECONDS = 1


@router.get(
    "/api/organizations/{org_id}/deployments",
    response_model=list[DeploymentRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_deployments(
    org_id: uuid.UUID,
    state: DeploymentState | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
) -> list[Deployment]:
    stmt = select(Deployment).where(Deployment.org_id == org_id, Deployment.deleted_at.is_(None))
    if state is not None:
        stmt = stmt.where(Deployment.state == state)
    if q:
        stmt = stmt.where(Deployment.hostname.ilike(f"%{q}%"))
    stmt = stmt.order_by(Deployment.created_at.desc()).limit(min(limit, 500)).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def _create_one(
    db: AsyncSession,
    org_id: uuid.UUID,
    current_user: User,
    *,
    template_id: uuid.UUID,
    hypervisor_host_id: uuid.UUID,
    hostname: str,
    ip_mode,
    static_ip: str | None,
    static_netmask: str | None,
    static_gateway: str | None,
    static_dns: list[str] | None,
) -> Deployment:
    """Adds a deployment (+ its audit/notification rows) to the session
    without committing or enqueueing the arq job, callers do both exactly
    once: the single-create route after one row, the bulk route after all
    of them."""
    deployment = Deployment(
        org_id=org_id,
        template_id=template_id,
        hypervisor_host_id=hypervisor_host_id,
        hostname=hostname,
        ip_mode=ip_mode,
        static_ip=static_ip,
        static_netmask=static_netmask,
        static_gateway=static_gateway,
        static_dns=static_dns,
        callback_token=secrets.token_urlsafe(32),
        created_by_user_id=current_user.id,
    )
    db.add(deployment)
    await db.flush()  # deployment.id must exist before the notification's FK references it

    audit.record(
        db,
        action="deployment.create",
        target_type="deployment",
        org_id=org_id,
        user_id=current_user.id,
        target_id=deployment.id,
        detail={"hostname": hostname},
    )
    notifications.notify(
        db,
        user_id=current_user.id,
        deployment_id=deployment.id,
        message=f"Deployment {hostname} started",
    )
    return deployment


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
    deployment = await _create_one(
        db,
        org_id,
        current_user,
        template_id=body.template_id,
        hypervisor_host_id=body.hypervisor_host_id,
        hostname=body.hostname,
        ip_mode=body.ip_mode,
        static_ip=body.static_ip,
        static_netmask=body.static_netmask,
        static_gateway=body.static_gateway,
        static_dns=body.static_dns,
    )
    await db.commit()
    await db.refresh(deployment)

    pool = await get_arq_pool()
    await notifications.maybe_email(
        db, pool, user_id=current_user.id, event_type="start",
        subject=f"Deployment {deployment.hostname} started",
        body=f"Deployment {deployment.hostname} has started provisioning.",
    )
    await webhooks.dispatch(
        db, pool, org_id, "deployment.start",
        {"deployment_id": str(deployment.id), "hostname": deployment.hostname, "state": deployment.state.value},
    )
    await pool.enqueue_job("run_deployment", str(deployment.id))
    return deployment


@router.post(
    "/api/organizations/{org_id}/deployments/bulk",
    response_model=list[DeploymentRead],
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def create_bulk_deployments(
    org_id: uuid.UUID,
    body: BulkDeploymentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[Deployment]:
    """DHCP only, deliberately: a static IP mode would need per-VM address
    allocation and collision handling that bulk deployment doesn't attempt.
    Create static-IP deployments individually instead."""
    if body.count < 1 or body.count > 50:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "count must be between 1 and 50")

    deployments = []
    for i in range(1, body.count + 1):
        deployment = await _create_one(
            db,
            org_id,
            current_user,
            template_id=body.template_id,
            hypervisor_host_id=body.hypervisor_host_id,
            hostname=f"{body.hostname_prefix}{i:02d}",
            ip_mode=IpMode.DHCP,
            static_ip=None,
            static_netmask=None,
            static_gateway=None,
            static_dns=None,
        )
        deployments.append(deployment)
    await db.commit()

    pool = await get_arq_pool()
    for deployment in deployments:
        await db.refresh(deployment)
        await notifications.maybe_email(
            db, pool, user_id=current_user.id, event_type="start",
            subject=f"Deployment {deployment.hostname} started",
            body=f"Deployment {deployment.hostname} has started provisioning.",
        )
        await webhooks.dispatch(
            db, pool, org_id, "deployment.start",
            {"deployment_id": str(deployment.id), "hostname": deployment.hostname, "state": deployment.state.value},
        )
        await pool.enqueue_job("run_deployment", str(deployment.id))
    return deployments


async def _get_org_deployment(
    db: AsyncSession, org_id: uuid.UUID, deployment_id: uuid.UUID, *, include_deleted: bool = False
) -> Deployment:
    stmt = select(Deployment).where(Deployment.id == deployment_id, Deployment.org_id == org_id)
    if not include_deleted:
        stmt = stmt.where(Deployment.deleted_at.is_(None))
    result = await db.execute(stmt)
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
    # include_deleted: the deployment itself disappears from lists/detail
    # once deleted, but its history/logs stay fetchable directly by id
    # (see DELETE .../deployments/{deployment_id} below), so this
    # shouldn't 404 just because the deployment was deleted.
    await _get_org_deployment(db, org_id, deployment_id, include_deleted=True)
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
    await _get_org_deployment(db, org_id, deployment_id, include_deleted=True)
    result = await db.execute(
        select(DeploymentLogLine).where(DeploymentLogLine.deployment_id == deployment_id).order_by(DeploymentLogLine.ts)
    )
    return list(result.scalars().all())


@router.get(
    "/api/organizations/{org_id}/deployments/{deployment_id}/health-history",
    response_model=list[DeploymentHealthCheckRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def get_deployment_health_history(
    org_id: uuid.UUID, deployment_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> list[DeploymentHealthCheck]:
    await _get_org_deployment(db, org_id, deployment_id)
    result = await db.execute(
        select(DeploymentHealthCheck)
        .where(DeploymentHealthCheck.deployment_id == deployment_id)
        .order_by(DeploymentHealthCheck.checked_at.desc())
        .limit(200)
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
    await webhooks.dispatch(
        db, pool, org_id, "deployment.retry",
        {"deployment_id": str(deployment.id), "hostname": deployment.hostname},
    )
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
    "/api/organizations/{org_id}/deployments/{deployment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
async def delete_deployment(
    org_id: uuid.UUID,
    deployment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Soft delete: hides the deployment from lists, the dashboard, and its
    own detail page, but its row, log lines, state transitions, and health
    checks all stay in the database untouched, still reachable through
    /history and /logs above by anyone who has the id. Doesn't touch the
    hypervisor at all: if a VM exists it's simply left running, untracked
    by DeployCore from here on (there's no separate "delete just the VM"
    action, remove it directly on the hypervisor if you want it gone too).
    Allowed at any stage, not just terminal ones: if the pipeline is still
    actively running, it keeps running in the background regardless (this
    doesn't cancel it), just no longer visible anywhere in the UI."""
    deployment = await _get_org_deployment(db, org_id, deployment_id)
    deployment.deleted_at = utcnow()
    audit.record(
        db, action="deployment.delete", target_type="deployment",
        org_id=org_id, user_id=current_user.id, target_id=deployment.id,
        detail={
            "hostname": deployment.hostname,
            "state": deployment.state.value,
            "vm_left_running": deployment.vm_moref is not None,
        },
    )
    await db.commit()


async def _event_stream(deployment_id: uuid.UUID, request: Request):
    """Owns its own DB session rather than reusing the request's, a
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
