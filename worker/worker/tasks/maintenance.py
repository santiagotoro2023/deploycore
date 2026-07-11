import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.hypervisors import get_driver
from app.models.deployment import Deployment, DeploymentHealthCheck, DeploymentState, HealthStatus
from app.models.hypervisor import HypervisorHost
from app.services import notifications, settings_resolver, webhooks
from app.services.deployment_service import log

TERMINAL_STATES = (DeploymentState.COMPLETED, DeploymentState.FAILED)
HEALTH_HISTORY_RETENTION_DAYS = 30

# provision.py's last post-install step closes WinRM for good (stops the
# service, drops the firewall rule), so a completed deployment can no
# longer be reachability-checked over WinRM even if credentials are
# available. RDP (3389) is on by default on every Windows Server SKU and
# doesn't need anything guest-side re-opened for DeployCore to probe it, a
# plain TCP connect is enough: it doesn't prove WinRM/PowerShell remoting
# still works (nothing does, on purpose), only that the guest OS is up and
# reachable on the network, the same thing "healthy" meant before, just
# without needing standing remote-admin access to check it.
HEALTH_CHECK_PORT = 3389
HEALTH_CHECK_TIMEOUT_SECONDS = 5


async def _tcp_reachable(ip: str, port: int, timeout: float) -> bool:
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


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
            new_status = HealthStatus.UNKNOWN
            try:
                driver = get_driver(host)
                ip = deployment.static_ip if deployment.static_ip else await driver.get_guest_ip(deployment.vm_moref)
                if ip:
                    reachable = await _tcp_reachable(ip, HEALTH_CHECK_PORT, HEALTH_CHECK_TIMEOUT_SECONDS)
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
                await notifications.dispatch(
                    db, ctx["redis"], user_id=deployment.created_by_user_id, event_type="health_degraded",
                    context={"hostname": deployment.hostname, "checked_at": checked_at.isoformat()},
                )
                await webhooks.dispatch(
                    db, ctx["redis"], deployment.org_id, "health.degraded",
                    {"deployment_id": str(deployment.id), "hostname": deployment.hostname},
                )

        cutoff = datetime.now(timezone.utc) - timedelta(days=HEALTH_HISTORY_RETENTION_DAYS)
        await db.execute(delete(DeploymentHealthCheck).where(DeploymentHealthCheck.checked_at < cutoff))
        await db.commit()
