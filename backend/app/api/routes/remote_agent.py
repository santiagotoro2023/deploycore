from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_db
from app.models.base import utcnow
from app.models.managed_host import ManagedHost
from app.models.user import Role
from app.schemas.managed_host import (
    ManagedHostEnrollRequest,
    RemoteAgentConfig,
    RemotePort,
    RemoteStatus,
)
from app.security.rbac import require_role
from app.services import remote_desktop

router = APIRouter(prefix="/api/remote", tags=["remote-agent"])

_INSTALL_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "services" / "remote_agent_install.ps1"


async def _host_for_token(db: AsyncSession, enroll_token: str) -> ManagedHost:
    result = await db.execute(select(ManagedHost).where(ManagedHost.enroll_token == enroll_token))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown enrollment token")
    return host


@router.get("/status", response_model=RemoteStatus, dependencies=[Depends(require_role(Role.READONLY, org_scoped=False))])
async def remote_status() -> RemoteStatus:
    """Instance-level (not org-scoped) readiness for the Remote Management
    setup banner - whether the self-hosted RustDesk stack is configured and
    reachable, plus the relay host and ports the user must forward/allow."""
    configured, reachable, detail = await remote_desktop.probe()
    settings = get_settings()
    return RemoteStatus(
        configured=configured,
        reachable=reachable,
        detail=detail,
        relay_host=settings.rustdesk_relay_host,
        ports=[RemotePort(**p) for p in remote_desktop.RELAY_PORTS],
    )


def _read_server_key() -> str:
    """The hbbs public key the agent must trust. Missing = the rustdesk
    container hasn't finished first-run init yet (it generates the key into
    its data volume, which we mount read-only) - a 503, not a 500, so the
    installer/banner can tell "not ready yet" from a real error."""
    path = Path(get_settings().rustdesk_key_file)
    if not path.exists():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Remote Management server key isn't ready yet")
    return path.read_text().strip()


@router.get("/agent-config/{enroll_token}", response_model=RemoteAgentConfig)
async def agent_config(enroll_token: str, db: AsyncSession = Depends(get_db)) -> RemoteAgentConfig:
    """Called by the agent installer (authenticated by its own enroll token,
    no user session) to self-configure a stock RustDesk client for this
    instance's server - so the relay address and server key never have to be
    copied by hand."""
    await _host_for_token(db, enroll_token)
    relay_host = get_settings().rustdesk_relay_host
    return RemoteAgentConfig(
        relay_host=relay_host,
        id_server=f"{relay_host}:21116",
        relay_server=f"{relay_host}:21117",
        key=_read_server_key(),
    )


@router.get("/install-script")
async def install_script() -> Response:
    """Serves the PowerShell agent installer, with this instance's own URL
    baked in as the default server, so the copy-paste one-liner on the Remote
    Management tab only has to carry the enroll token. Unauthenticated on
    purpose: it's fetched by `irm <url>/api/remote/install-script | iex` before
    any session exists, and carries no secret itself (the token comes from the
    caller's environment). The script is app/services/remote_agent_install.ps1."""
    try:
        script = _INSTALL_SCRIPT_PATH.read_text()
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "install script not found")
    script = script.replace("__DEPLOYCORE_SERVER__", get_settings().app_public_url)
    return Response(content=script, media_type="text/plain")


@router.post("/enroll/{enroll_token}", status_code=status.HTTP_204_NO_CONTENT)
async def enroll_agent(
    enroll_token: str, body: ManagedHostEnrollRequest, db: AsyncSession = Depends(get_db)
) -> None:
    """Authenticated by the single-use per-host token itself, not a user
    session - the caller is the agent's own enrollment script running on
    whatever machine it was just installed on, not an operator. Called
    exactly once, right after the (rebranded RustDesk) agent finishes
    installing: reports the RustDesk-assigned ID and a locally-generated
    permanent/unattended-access password, both minted on the machine
    itself rather than by DeployCore (see ManagedHost's own docstring for
    why). Safe to call again later too (e.g. the agent reinstalled and
    got a new RustDesk ID) - just overwrites what's on file rather than
    rejecting a second call, unlike the Setup-complete callback this
    otherwise mirrors, there's no equivalent "only means anything once"
    invariant here to protect."""
    host = await _host_for_token(db, enroll_token)
    host.rustdesk_id = body.rustdesk_id
    host.rustdesk_key = body.rustdesk_key
    host.enrolled = True
    host.last_seen_at = utcnow()
    await db.commit()
