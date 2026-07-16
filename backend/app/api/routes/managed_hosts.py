import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.app_asset import AppAsset
from app.models.managed_host import ManagedHost
from app.models.user import Role, User
from app.schemas.managed_host import (
    ManagedHostCreate,
    ManagedHostRdpCredentials,
    ManagedHostRead,
    ManagedHostSession,
    ManagedHostUpdate,
)
from app.security.rbac import get_current_user, require_role
from app.services import audit, remote_desktop

router = APIRouter(tags=["managed-hosts"])


async def _get_org_managed_host(db: AsyncSession, org_id: uuid.UUID, host_id: uuid.UUID) -> ManagedHost:
    host = await db.get(ManagedHost, host_id)
    if host is None or host.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "host not found in this organization")
    return host


@router.get(
    "/api/organizations/{org_id}/managed-hosts",
    response_model=list[ManagedHostRead],
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def list_managed_hosts(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[ManagedHost]:
    result = await db.execute(select(ManagedHost).where(ManagedHost.org_id == org_id))
    return list(result.scalars().all())


@router.post(
    "/api/organizations/{org_id}/managed-hosts",
    response_model=ManagedHostRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def create_managed_host(
    org_id: uuid.UUID, body: ManagedHostCreate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManagedHost:
    host = ManagedHost(
        org_id=org_id, deployment_id=body.deployment_id, name=body.name, created_by_user_id=current_user.id,
    )
    db.add(host)
    await db.flush()
    audit.record(
        db, action="managed_host.create", target_type="managed_host", org_id=org_id,
        user_id=current_user.id, target_id=host.id, detail={"name": host.name},
    )
    await db.commit()
    await db.refresh(host)
    return host


@router.get(
    "/api/organizations/{org_id}/managed-hosts/agent-installer",
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def download_agent_installer(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Serves the one seeded, global DeployCore Remote Management Agent
    installer (see services/remote_agent.py) - the same file regardless
    of which host it'll be run for, since the file itself carries no
    per-host secret at all (see ManagedHost's own docstring). The
    Remote Management page pairs this download with a copyable install
    command that carries the specific host's enroll_token as a
    command-line argument instead.

    Registered before the /{host_id} routes below - FastAPI matches
    routes in declaration order, and "agent-installer" would otherwise
    be swallowed by the {host_id}: uuid.UUID path converter and fail
    with a UUID parse error before ever reaching this handler."""
    result = await db.execute(select(AppAsset).where(AppAsset.is_remote_agent.is_(True)))
    agent_asset = result.scalars().first()
    if agent_asset is None or not agent_asset.storage_path or not Path(agent_asset.storage_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "remote management agent installer is not available yet")
    return FileResponse(agent_asset.storage_path, filename=agent_asset.filename, media_type="application/octet-stream")


@router.get(
    "/api/organizations/{org_id}/managed-hosts/{host_id}",
    response_model=ManagedHostRead,
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def get_managed_host(org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ManagedHost:
    return await _get_org_managed_host(db, org_id, host_id)


@router.post(
    "/api/organizations/{org_id}/managed-hosts/{host_id}/session",
    response_model=ManagedHostSession,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def start_managed_host_session(
    org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManagedHostSession:
    """Mints a fresh, short-lived embeddable web-client URL for this host
    (see services/remote_desktop.py). Operator+, org-scoped like everything
    else here - the org-membership check in _get_org_managed_host is exactly
    what enforces "an admin of org X can only connect to org X's hosts."
    Not cached: each click gets its own one-time share link rather than
    reusing a stale one, and the host's current password is re-synced to the
    RustDesk side as a side effect."""
    host = await _get_org_managed_host(db, org_id, host_id)
    if not host.enrolled or not host.rustdesk_id or host.rustdesk_key is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "this host's agent hasn't enrolled yet")
    public_url = remote_desktop.public_url_for()
    try:
        embed_url = await remote_desktop.create_session_url(host.rustdesk_id, host.rustdesk_key, host.name, public_url)
    except remote_desktop.RemoteDesktopError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc))
    audit.record(
        db, action="managed_host.session", target_type="managed_host", org_id=org_id,
        user_id=current_user.id, target_id=host.id, detail={"name": host.name},
    )
    await db.commit()
    return ManagedHostSession(embed_url=embed_url)


@router.get(
    "/api/organizations/{org_id}/managed-hosts/{host_id}/rdp-credentials",
    response_model=ManagedHostRdpCredentials,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def get_managed_host_rdp_credentials(
    org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManagedHostRdpCredentials:
    """The ONLY route that ever returns the plaintext RDP password - not the
    list/get routes (see ManagedHostRead.rdp_password_set) - fetched by the
    frontend right when "Connect" is clicked, to attempt auto-typing it into
    the remote session's own login screen. Audit-logged same as starting a
    session itself, since reading a plaintext credential is exactly the kind
    of action worth a trail."""
    host = await _get_org_managed_host(db, org_id, host_id)
    audit.record(
        db, action="managed_host.rdp_credentials_viewed", target_type="managed_host", org_id=org_id,
        user_id=current_user.id, target_id=host.id, detail={"name": host.name},
    )
    await db.commit()
    return ManagedHostRdpCredentials(username=host.rdp_username, password=host.rdp_password)


@router.patch(
    "/api/organizations/{org_id}/managed-hosts/{host_id}",
    response_model=ManagedHostRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def update_managed_host(
    org_id: uuid.UUID, host_id: uuid.UUID, body: ManagedHostUpdate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ManagedHost:
    host = await _get_org_managed_host(db, org_id, host_id)
    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(host, field, value)
    # Never write the plaintext rdp_password into the audit trail - swap it
    # for whether it was set/cleared, same as ManagedHostRead never returns
    # the plaintext over a routine read either.
    audit_detail = {k: v for k, v in updates.items() if k != "rdp_password"}
    if "rdp_password" in updates:
        audit_detail["rdp_password"] = "cleared" if not updates["rdp_password"] else "changed"
    audit.record(
        db, action="managed_host.update", target_type="managed_host", org_id=org_id,
        user_id=current_user.id, target_id=host.id, detail=audit_detail,
    )
    await db.commit()
    await db.refresh(host)
    return host


@router.delete(
    "/api/organizations/{org_id}/managed-hosts/{host_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def delete_managed_host(
    org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Only removes DeployCore's own record of the host - same as
    deleting a Deployment doesn't reach out and tear down the VM, this
    doesn't reach out and uninstall the agent or deregister it from the
    self-hosted RustDesk server. The agent keeps running and stays
    connectable by its raw ID/key outside of DeployCore's UI either way;
    genuinely removing remote access requires uninstalling the agent on
    the machine itself."""
    host = await _get_org_managed_host(db, org_id, host_id)
    audit.record(
        db, action="managed_host.delete", target_type="managed_host", org_id=org_id,
        user_id=current_user.id, target_id=host.id, detail={"name": host.name},
    )
    await db.delete(host)
    await db.commit()
