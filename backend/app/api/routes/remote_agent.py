import json
import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models.base import utcnow
from app.models.managed_host import ManagedHost
from app.models.user import Role
from app.schemas.managed_host import (
    ManagedHostEnrollResponse,
    RemoteAgentConfig,
    RemotePort,
    RemoteStatus,
)
from app.security.rbac import require_role
from app.services import remote_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/remote", tags=["remote-agent"])

_INSTALL_SCRIPT_PATH = Path(__file__).resolve().parent.parent.parent / "services" / "remote_agent_install.ps1"


async def _host_for_token(db: AsyncSession, enroll_token: str) -> ManagedHost:
    result = await db.execute(select(ManagedHost).where(ManagedHost.enroll_token == enroll_token))
    host = result.scalar_one_or_none()
    if host is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown enrollment token")
    return host


@router.get("/status", response_model=RemoteStatus, dependencies=[Depends(require_role(Role.READONLY, org_scoped=False))])
async def remote_status(db: AsyncSession = Depends(get_db)) -> RemoteStatus:
    """Instance-level (not org-scoped) readiness for the Remote Management
    setup banner - whether coturn/guacd are configured and reachable, plus
    the relay host and ports the user must forward/allow (non-LAN access
    only - see RELAY_PORTS)."""
    configured, reachable, detail = await remote_session.probe()
    return RemoteStatus(
        configured=configured,
        reachable=reachable,
        detail=detail,
        relay_host=await remote_session.resolve_public_host(db),
        ports=[RemotePort(**p) for p in remote_session.RELAY_PORTS],
        app_public_url=await remote_session.resolve_app_public_url(db),
    )


@router.get("/agent-config/{enroll_token}", response_model=RemoteAgentConfig)
async def agent_config(enroll_token: str, db: AsyncSession = Depends(get_db)) -> RemoteAgentConfig:
    """Called by the agent installer (authenticated by its own enroll token,
    no user session) so the agent can build its own ICE server list for the
    Shadow WebRTC path - nothing else to configure, the control channel
    itself just connects to this same instance's own origin."""
    await _host_for_token(db, enroll_token)
    settings = get_settings()
    return RemoteAgentConfig(
        turn_host=await remote_session.resolve_public_host(db),
        turn_port=3478,
        turn_username=settings.turn_username,
        turn_password=settings.turn_password,
    )


@router.get("/install-script")
async def install_script(db: AsyncSession = Depends(get_db)) -> Response:
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
    script = script.replace("__DEPLOYCORE_SERVER__", await remote_session.resolve_app_public_url(db))
    return Response(content=script, media_type="text/plain")


@router.post("/enroll/{enroll_token}", response_model=ManagedHostEnrollResponse)
async def enroll_agent(enroll_token: str, db: AsyncSession = Depends(get_db)) -> ManagedHostEnrollResponse:
    """Authenticated by the single-use... no, the PERMANENT per-host token
    itself (see ManagedHost's own docstring - "single-use" stopped being
    true once it became the control channel's own identifier too), not a
    user session - the caller is the agent's own enrollment script running
    on whatever machine it was just installed on, not an operator.

    DeployCore mints agent_key HERE and hands it back - the agent never
    chooses or reports its own long-term credential (contrast the old
    RustDesk-based version, where the agent generated its own password and
    reported it home). Safe to call again later (e.g. the agent was
    reinstalled) - just mints and stores a fresh key rather than rejecting a
    second call, same as before."""
    host = await _host_for_token(db, enroll_token)
    agent_key = secrets.token_hex(32)
    host.agent_key = agent_key
    host.enrolled = True
    host.last_seen_at = utcnow()
    await db.commit()
    return ManagedHostEnrollResponse(agent_key=agent_key)


@router.websocket("/agent-control")
async def agent_control(websocket: WebSocket) -> None:
    """The agent's one persistent outbound connection (see
    remote-agent/PROTOCOL.md) - authenticated at the HTTP upgrade via
    X-Enroll-Token/X-Agent-Key headers (a real client like the agent can set
    these; a browser can't, which is exactly why the operator-facing session
    route in managed_hosts.py uses a query-param token instead). Rejected
    before accept() on any mismatch, so an invalid caller never gets a
    session at all, not just a closed one."""
    enroll_token = websocket.headers.get("x-enroll-token")
    agent_key = websocket.headers.get("x-agent-key")
    if not enroll_token or not agent_key:
        await websocket.close(code=4401)
        return

    async with SessionLocal() as db:
        result = await db.execute(select(ManagedHost).where(ManagedHost.enroll_token == enroll_token))
        host = result.scalar_one_or_none()
        if host is None or not host.enrolled or host.agent_key != agent_key:
            await websocket.close(code=4401)
            return
        host_id = host.id
        host.last_seen_at = utcnow()
        await db.commit()

    await websocket.accept()
    remote_session.register_agent(host_id, websocket)
    logger.info("Agent control channel connected for host %s", host_id)
    try:
        while True:
            raw = await websocket.receive()
            if raw["type"] == "websocket.disconnect":
                break
            if (text := raw.get("text")) is not None:
                message = json.loads(text)
                mtype = message.get("type")
                if mtype == "heartbeat":
                    async with SessionLocal() as db:
                        result = await db.execute(select(ManagedHost).where(ManagedHost.id == host_id))
                        current = result.scalar_one_or_none()
                        if current is not None:
                            current.last_seen_at = utcnow()
                            await db.commit()
                elif mtype == "signal":
                    session = remote_session.get_session(message.get("session_id", ""))
                    if session is not None:
                        await session.websocket.send_json(message)
                elif mtype == "session_end":
                    # The agent itself ending a session unprompted (its local
                    # RDP loopback socket closed, a Shadow session's capture
                    # died, etc.) - not just the operator disconnecting, which
                    # already tears itself down via managed_hosts.py's own
                    # WebSocket handler. Close the browser's side so it finds
                    # out immediately instead of only noticing on its next
                    # failed send.
                    session = remote_session.get_session(message.get("session_id", ""))
                    if session is not None:
                        try:
                            await session.websocket.close(code=4410, reason="session ended by agent")
                        except Exception:  # noqa: BLE001 - browser side may already be gone
                            pass
            elif (data := raw.get("bytes")) is not None:
                # Connect mode only (Shadow never sends binary control-channel
                # frames - its media/input goes over WebRTC directly, see
                # PROTOCOL.md) - routes to the local writer bridging guacd's
                # own RDP connection, never to a browser WebSocket directly.
                if len(data) < 16:
                    continue
                session_id = data[:16].hex()
                writer = remote_session.get_tunnel_writer(session_id)
                if writer is not None:
                    writer.write(data[16:])
                    await writer.drain()
    except WebSocketDisconnect:
        pass
    finally:
        remote_session.unregister_agent(host_id)
        logger.info("Agent control channel disconnected for host %s", host_id)
