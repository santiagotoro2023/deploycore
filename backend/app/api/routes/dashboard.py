from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.deployment import Deployment, DeploymentState
from app.models.hypervisor import ConnectionStatus, HypervisorHost
from app.models.org import Organization
from app.models.user import Role
from app.schemas.dashboard import OrgOverview
from app.security.rbac import require_role

router = APIRouter(tags=["dashboard"])

_RUNNING_STATES = [s for s in DeploymentState if s not in (DeploymentState.COMPLETED, DeploymentState.FAILED)]


@router.get(
    "/api/dashboard/overview",
    response_model=list[OrgOverview],
    dependencies=[Depends(require_role(Role.ADMIN, org_scoped=False))],
)
async def dashboard_overview(db: AsyncSession = Depends(get_db)) -> list[OrgOverview]:
    """Cross-org summary for MSP admins — a handful of count queries per
    org rather than one large join; simplest correct choice at the scale
    of an MSP's customer-organization count."""
    orgs = (await db.execute(select(Organization).order_by(Organization.name))).scalars().all()
    overview = []
    for org in orgs:
        running = await db.scalar(
            select(func.count()).select_from(Deployment).where(
                Deployment.org_id == org.id, Deployment.state.in_(_RUNNING_STATES)
            )
        )
        completed = await db.scalar(
            select(func.count()).select_from(Deployment).where(
                Deployment.org_id == org.id, Deployment.state == DeploymentState.COMPLETED
            )
        )
        failed = await db.scalar(
            select(func.count()).select_from(Deployment).where(
                Deployment.org_id == org.id, Deployment.state == DeploymentState.FAILED
            )
        )
        hypervisors_total = await db.scalar(
            select(func.count()).select_from(HypervisorHost).where(HypervisorHost.org_id == org.id)
        )
        hypervisors_ok = await db.scalar(
            select(func.count()).select_from(HypervisorHost).where(
                HypervisorHost.org_id == org.id, HypervisorHost.last_test_status == ConnectionStatus.OK
            )
        )
        overview.append(
            OrgOverview(
                org_id=org.id,
                org_name=org.name,
                running=running or 0,
                completed=completed or 0,
                failed=failed or 0,
                hypervisors_ok=hypervisors_ok or 0,
                hypervisors_total=hypervisors_total or 0,
            )
        )
    return overview
