from sqlalchemy.ext.asyncio import AsyncSession

from app.models.deployment import (
    Deployment,
    DeploymentLogLine,
    DeploymentState,
    DeploymentStateTransition,
    LogLevel,
)

ALLOWED_TRANSITIONS = {
    DeploymentState.PENDING: {DeploymentState.CREATING_VM, DeploymentState.FAILED},
    DeploymentState.CREATING_VM: {DeploymentState.BOOTING, DeploymentState.FAILED},
    DeploymentState.BOOTING: {DeploymentState.INSTALLING_OS, DeploymentState.FAILED},
    DeploymentState.INSTALLING_OS: {DeploymentState.POST_INSTALL, DeploymentState.FAILED},
    DeploymentState.POST_INSTALL: {DeploymentState.CONFIGURING, DeploymentState.FAILED},
    DeploymentState.CONFIGURING: {DeploymentState.COMPLETED, DeploymentState.FAILED},
}

TERMINAL_STATES = {DeploymentState.COMPLETED, DeploymentState.FAILED}


class InvalidTransition(Exception):
    pass


class DeploymentStateMachine:
    async def transition(
        self,
        db: AsyncSession,
        deployment: Deployment,
        to_state: DeploymentState,
        detail: str | None = None,
    ) -> None:
        if deployment.state in TERMINAL_STATES:
            raise InvalidTransition(f"deployment already in terminal state {deployment.state.value}")
        allowed = ALLOWED_TRANSITIONS.get(deployment.state, set())
        if to_state != DeploymentState.FAILED and to_state not in allowed:
            raise InvalidTransition(f"{deployment.state.value} -> {to_state.value} is not allowed")

        db.add(
            DeploymentStateTransition(
                deployment_id=deployment.id,
                from_state=deployment.state.value,
                to_state=to_state.value,
                detail=detail,
            )
        )
        deployment.state = to_state
        if to_state == DeploymentState.FAILED and detail:
            deployment.error_message = detail
        await db.commit()


async def log(
    db: AsyncSession, deployment: Deployment, stage: str, message: str, level: LogLevel = LogLevel.INFO
) -> None:
    db.add(DeploymentLogLine(deployment_id=deployment.id, stage=stage, level=level, message=message))
    await db.commit()


async def retry_deployment(db: AsyncSession, deployment: Deployment) -> None:
    """Full retry from `pending`, safe by construction: DeployCore never
    reuses a partially-created VM, so there's nothing stale to collide with
    (the pipeline's own cleanup step deletes any partial VM before marking
    a deployment failed)."""
    if deployment.state != DeploymentState.FAILED:
        raise InvalidTransition("only a failed deployment can be retried")
    db.add(
        DeploymentStateTransition(
            deployment_id=deployment.id,
            from_state=deployment.state.value,
            to_state=DeploymentState.PENDING.value,
            detail=f"retry #{deployment.retry_count + 1}",
        )
    )
    deployment.state = DeploymentState.PENDING
    deployment.error_message = None
    deployment.retry_count += 1
    deployment.vm_moref = None
    deployment.answer_iso_remote_path = None
    deployment.callback_token_used = False
    await db.commit()
