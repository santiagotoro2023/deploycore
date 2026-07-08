from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.deployment import Deployment, DeploymentState
from app.services.deployment_service import DeploymentStateMachine

router = APIRouter(prefix="/api/callback", tags=["callback"])


@router.post("/{deployment_token}", status_code=status.HTTP_204_NO_CONTENT)
async def deployment_callback(deployment_token: str, db: AsyncSession = Depends(get_db)) -> None:
    """Authenticated by the single-use per-deployment token itself, not by
    a user session, the caller is the guest VM's FirstLogonCommands step,
    not an operator."""
    result = await db.execute(select(Deployment).where(Deployment.callback_token == deployment_token))
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown callback token")
    if deployment.callback_token_used:
        raise HTTPException(status.HTTP_409_CONFLICT, "callback token already used")
    if deployment.state != DeploymentState.BOOTING:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"deployment is not awaiting a callback (state={deployment.state.value})"
        )

    deployment.callback_token_used = True
    await DeploymentStateMachine().transition(
        db, deployment, DeploymentState.INSTALLING_OS, detail="guest callback received"
    )
