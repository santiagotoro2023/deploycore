import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.hypervisors import get_driver
from app.models.deployment import Deployment, DeploymentHealthCheck, DeploymentState, HealthStatus
from app.models.hypervisor import HypervisorHost
from app.models.template import DeploymentTemplate
from app.services import notifications, settings_resolver, webhooks
from app.services.deployment_service import log
from app.winrm.client import WinRMClient

TERMINAL_STATES = (DeploymentState.COMPLETED, DeploymentState.FAILED)
HEALTH_HISTORY_RETENTION_DAYS = 30


async def sweep_stale_deployments(ctx) -> None:
    """Cron job: force-fails deployments stuck past their stage timeout.
    `updated_at` doubles as "time of last state transition" since every
    DeploymentStateMachine.transition() touches the row."""
    async with SessionLocal() as db:
        result = await db.execute(select(Deployment).where(Deployment.state.notin_(TERMINAL_STATES)))
        stale_ids = []
        for deployment in result.scalars().all():
            timeout_minutes = await settings_resolver.resolve(
                db,
                "os_install_timeout_minutes",
                org_id=deployment.org_id,
                template_id=deployment.template_id,
            )
            deadline = deployment.updated_at + timedelta(minutes=timeout_minutes)
            if datetime.now(timezone.utc) > deadline:
                stale_ids.append(str(deployment.id))

    for deployment_id in stale_ids:
        await ctx["redis"].enqueue_job(
            "cleanup_deployment", deployment_id, "timed out: stale in a non-terminal state past its stage timeout"
        )


async def check_deployment_health(ctx) -> None:
    """Cron job: periodic reachability check for completed deployments.
    Writes both the latest-result columns on Deployment and an append-only
    DeploymentHealthCheck history row (pruned past
    HEALTH_HISTORY_RETENTION_DAYS). Only logs a deployment log line when the
    status actually changes, so this doesn't flood the deployment log every
    cycle."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(Deployment).where(Deployment.state == DeploymentState.COMPLETED, Deployment.vm_moref.isnot(None))
        )
        deployments = result.scalars().all()

        for deployment in deployments:
            previous_status = deployment.last_health_status
            host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
            template = await db.get(DeploymentTemplate, deployment.template_id)
            new_status = HealthStatus.UNKNOWN
            try:
                driver = get_driver(host)
                ip = deployment.static_ip if deployment.static_ip else await driver.get_guest_ip(deployment.vm_moref)
                if ip:
                    client = WinRMClient(ip, "Administrator", template.local_admin_password)
                    reachable = await asyncio.to_thread(client.is_reachable)
                    new_status = HealthStatus.HEALTHY if reachable else HealthStatus.UNREACHABLE
                else:
                    new_status = HealthStatus.UNREACHABLE
            except Exception:  # noqa: BLE001 - a failed check just means "unreachable this cycle"
                new_status = HealthStatus.UNREACHABLE

            checked_at = datetime.now(timezone.utc)
            deployment.last_health_status = new_status
            deployment.last_health_checked_at = checked_at
            db.add(DeploymentHealthCheck(deployment_id=deployment.id, status=new_status, checked_at=checked_at))
            if new_status != previous_status and previous_status != HealthStatus.UNKNOWN:
                await log(db, deployment, "health_check", f"health check: {previous_status.value} -> {new_status.value}")
            await db.commit()

            if new_status == HealthStatus.UNREACHABLE and previous_status == HealthStatus.HEALTHY:
                await notifications.maybe_email(
                    db, ctx["redis"], user_id=deployment.created_by_user_id, event_type="health_degraded",
                    subject=f"Deployment {deployment.hostname} became unreachable",
                    body=f"Deployment {deployment.hostname} was healthy and is now unreachable as of {checked_at.isoformat()}.",
                )
                await webhooks.dispatch(
                    db, ctx["redis"], deployment.org_id, "health.degraded",
                    {"deployment_id": str(deployment.id), "hostname": deployment.hostname},
                )

        cutoff = datetime.now(timezone.utc) - timedelta(days=HEALTH_HISTORY_RETENTION_DAYS)
        await db.execute(delete(DeploymentHealthCheck).where(DeploymentHealthCheck.checked_at < cutoff))
        await db.commit()
