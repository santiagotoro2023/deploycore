from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models.deployment import Deployment, DeploymentState
from app.services import settings_resolver

TERMINAL_STATES = (DeploymentState.COMPLETED, DeploymentState.FAILED)


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
