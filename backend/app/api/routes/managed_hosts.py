import asyncio
import json
import logging
import uuid
from pathlib import Path

import jwt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.models.app_asset import AppAsset
from app.models.managed_host import ManagedHost
from app.models.user import ROLE_ORDER, Role, User
from app.redis import get_redis
from app.schemas.managed_host import (
    ManagedHostCreate,
    ManagedHostRdpCredentials,
    ManagedHostRead,
    ManagedHostUpdate,
)
from app.security.auth import decode_access_token
from app.security.rbac import get_current_user, require_role, resolve_effective_role
from app.services import audit, remote_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["managed-hosts"])

_DEFAULT_CONNECT_WIDTH = 1280
_DEFAULT_CONNECT_HEIGHT = 800


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
    installer (see services/remote_agent_seed.py) - the same file regardless
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
    "/api/organizations/{org_id}/managed-hosts/ice-servers",
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def get_ice_servers(org_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    """The browser's own RTCPeerConnection (Shadow mode) needs the same TURN
    credentials the agent uses (see remote_agent.py's agent-config) - ICE
    negotiates from both ends, and a relayed (non-LAN) connection only works
    if both sides can reach the same TURN server. Operator-gated rather than
    part of the instance-wide /api/remote/status probe - these are
    long-lived shared credentials, not scoped to one session, so this stays
    behind the same role check as opening a session at all.

    Registered before the /{host_id} routes below - same reason as
    agent-installer above: FastAPI matches routes in declaration order, and
    "ice-servers" would otherwise be swallowed by the {host_id}: uuid.UUID
    path converter and fail with a parse error first."""
    settings = get_settings()
    return {
        "turn_host": await remote_session.resolve_public_host(db),
        "turn_port": 3478,
        "turn_username": settings.turn_username,
        "turn_password": settings.turn_password,
    }


@router.get(
    "/api/organizations/{org_id}/managed-hosts/{host_id}",
    response_model=ManagedHostRead,
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def get_managed_host(org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> ManagedHost:
    return await _get_org_managed_host(db, org_id, host_id)


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
    list/get routes (see ManagedHostRead.rdp_password_set). Connect mode's
    own session route (below) reads these directly rather than through this
    route (it never leaves the backend for a real Connect session - guacd
    gets it straight from here), so this route now exists only for the
    credentials panel's "copy" affordance - still audit-logged, since
    surfacing a plaintext credential to an operator is worth a trail either
    way it happens."""
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
    """Only removes DeployCore's own record of the host - same as deleting a
    Deployment doesn't reach out and tear down the VM, this doesn't reach
    out and uninstall the agent. The agent keeps running and will keep
    trying to reconnect its control channel, but that reconnect is rejected
    from here on (its agent_key no longer matches any row) - no separate
    revocation step needed. Genuinely removing remote access requires
    uninstalling the agent on the machine itself."""
    host = await _get_org_managed_host(db, org_id, host_id)
    audit.record(
        db, action="managed_host.delete", target_type="managed_host", org_id=org_id,
        user_id=current_user.id, target_id=host.id, detail={"name": host.name},
    )
    await db.delete(host)
    await db.commit()


async def _authenticate_ws(
    websocket: WebSocket, org_id: uuid.UUID, host_id: uuid.UUID
) -> tuple[ManagedHost, str, str, int, int] | None:
    """Shared auth for the session WebSocket below - a query-param token,
    not the Authorization header require_role expects everywhere else: a
    browser's native WebSocket constructor can't set custom headers at all,
    so this is the standard way to carry a bearer token over a WS handshake.
    Returns (host, mode, rdp_username, rdp_password, width, height) or None
    (having already closed the socket with a specific code) on any failure."""
    token = websocket.query_params.get("token")
    mode = websocket.query_params.get("mode", "shadow")
    if mode not in ("shadow", "connect"):
        await websocket.close(code=4400)
        return None
    if not token:
        await websocket.close(code=4401)
        return None
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=4401)
        return None

    redis = get_redis()
    if not await redis.exists(f"session:{payload['sid']}"):
        await websocket.close(code=4401)
        return None

    async with SessionLocal() as db:
        user = await db.get(User, uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            await websocket.close(code=4401)
            return None
        effective_role = await resolve_effective_role(db, user, org_id)
        if ROLE_ORDER[effective_role] < ROLE_ORDER[Role.OPERATOR]:
            await websocket.close(code=4403)
            return None
        host = await db.get(ManagedHost, host_id)
        if host is None or host.org_id != org_id:
            await websocket.close(code=4404)
            return None
        if not host.enrolled:
            await websocket.close(code=4409)
            return None
        rdp_username, rdp_password = host.rdp_username, host.rdp_password

    width = int(websocket.query_params.get("w", _DEFAULT_CONNECT_WIDTH))
    height = int(websocket.query_params.get("h", _DEFAULT_CONNECT_HEIGHT))
    return host, mode, rdp_username, rdp_password, width, height


@router.websocket("/api/organizations/{org_id}/managed-hosts/{host_id}/session")
async def managed_host_session_ws(websocket: WebSocket, org_id: uuid.UUID, host_id: uuid.UUID) -> None:
    """The operator's session connection (see remote-agent/PROTOCOL.md and
    services/remote_session.py). Two independent things happen here
    depending on ?mode=:
      - shadow: a dumb relay for the WebRTC SDP/ICE handshake with the
        agent - once that completes, video/input flow directly between the
        browser and the agent; this socket carries only the initial
        handshake.
      - connect: this socket carries the raw Guacamole protocol for the
        WHOLE session - remote_session.open_guacd_connection() does the
        select/connect handshake with guacd on the operator's behalf (the
        browser never sees RDP credentials), then this route pumps bytes
        both ways for as long as the session lasts.

    Accepts FIRST, before any validation - confirmed against the ASGI spec
    itself (not assumed): a close() sent while still in the CONNECTING state
    (i.e. before accept()) makes the server respond with a bare HTTP 403 and
    never complete the handshake at all, so the browser never sees a real
    WebSocket close event with a reason - just a hard connection-refused
    with zero information. Confirmed live as exactly what was breaking
    Connect mode's error reporting (NS_ERROR_WEBSOCKET_CONNECTION_REFUSED in
    Firefox, no close reason visible anywhere), and it silently affected
    every other failure path here too - a bad token, insufficient role, an
    unenrolled host, a disconnected agent - not just this one. Accepting
    first means every close() below now delivers a normal, readable
    close event to ws.onclose in the browser instead."""
    await websocket.accept()

    auth = await _authenticate_ws(websocket, org_id, host_id)
    if auth is None:
        return
    host, mode, rdp_username, rdp_password, width, height = auth

    agent = remote_session.get_agent(host_id)
    if agent is None:
        await websocket.close(code=4503, reason="agent is not currently connected")
        return

    session_id_hex = uuid.uuid4().hex
    conn = remote_session.SessionConnection(session_id=session_id_hex, host_id=host_id, mode=mode, websocket=websocket)
    remote_session.register_session(conn)
    async with SessionLocal() as db:
        audit.record(
            db, action="managed_host.session", target_type="managed_host", org_id=org_id,
            user_id=None, target_id=host.id, detail={"name": host.name, "mode": mode},
        )
        await db.commit()
    await remote_session.send_to_agent(agent, {"type": "session_start", "session_id": session_id_hex, "mode": mode})

    try:
        if mode == "shadow":
            await _pump_shadow_signaling(websocket, agent, session_id_hex)
        else:
            await _pump_connect_tunnel(websocket, agent, session_id_hex, rdp_username, rdp_password, width, height)
    finally:
        remote_session.unregister_session(session_id_hex)
        try:
            await remote_session.send_to_agent(agent, {"type": "session_end", "session_id": session_id_hex})
        except Exception:  # noqa: BLE001 - agent may already be gone, session is ending either way
            pass


async def _pump_shadow_signaling(websocket: WebSocket, agent: remote_session.AgentConnection, session_id_hex: str) -> None:
    """Relays the browser's SDP offer answer / ICE candidates to the agent,
    tagged with this session id - the agent's own replies are pushed
    straight onto this same WebSocket by the agent-control handler's receive
    loop (remote_agent.py), not read back here."""
    try:
        while True:
            raw = await websocket.receive()
            if raw["type"] == "websocket.disconnect":
                break
            if (text := raw.get("text")) is not None:
                message = json.loads(text)
                message["type"] = "signal"
                message["session_id"] = session_id_hex
                await remote_session.send_to_agent(agent, message)
    except WebSocketDisconnect:
        pass


async def _pump_connect_tunnel(
    websocket: WebSocket,
    agent: remote_session.AgentConnection,
    session_id_hex: str,
    rdp_username: str | None,
    rdp_password: str | None,
    width: int,
    height: int,
) -> None:
    session_id_bytes = bytes.fromhex(session_id_hex)

    async def _bridge_listener_to_agent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """guacd connected to our ephemeral listener below - pump its bytes
        to/from the agent's tunneled RDP stream (binary control-channel
        frames tagged with this session's id)."""
        remote_session.register_tunnel_writer(session_id_hex, writer)
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                await remote_session.send_bytes_to_agent(agent, session_id_bytes + chunk)
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            remote_session.unregister_tunnel_writer(session_id_hex)
            writer.close()

    def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        asyncio.create_task(_bridge_listener_to_agent(reader, writer))

    listener = await asyncio.start_server(_handle_client, host="0.0.0.0", port=0)
    ephemeral_port = listener.sockets[0].getsockname()[1]

    try:
        guacd_reader, guacd_writer = await remote_session.open_guacd_connection(
            host="api", port=ephemeral_port, username=rdp_username, password=rdp_password, width=width, height=height,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as a close reason, not a stack trace to the browser
        logger.warning("Connect-mode session %s: guacd handshake failed: %s", session_id_hex, exc)
        listener.close()
        # A previous version of this used a fixed generic reason - real
        # guacd/FreeRDP failures (bad credentials, RDP disabled on the
        # target, a security-mode mismatch) need their actual text to reach
        # the operator, or every failure looks identical from the browser.
        # WebSocket close reasons are capped at 123 UTF-8 BYTES (RFC 6455's
        # 125-byte control-frame payload minus the 2-byte status code) -
        # truncated defensively rather than trusting every possible
        # exception message to already fit.
        reason = str(exc).encode("utf-8")[:120].decode("utf-8", errors="ignore")
        await websocket.close(code=4502, reason=reason or "could not start the RDP session")
        return

    async def _from_guacd_to_browser() -> None:
        try:
            while True:
                chunk = await guacd_reader.read(65536)
                if not chunk:
                    break
                await websocket.send_bytes(chunk)
        except Exception:  # noqa: BLE001 - browser disconnected or guacd went away, either way stop forwarding
            pass

    forward_task = asyncio.create_task(_from_guacd_to_browser())
    try:
        while True:
            raw = await websocket.receive()
            if raw["type"] == "websocket.disconnect":
                break
            if (data := raw.get("bytes")) is not None:
                guacd_writer.write(data)
                await guacd_writer.drain()
            elif (text := raw.get("text")) is not None:
                guacd_writer.write(text.encode())
                await guacd_writer.drain()
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        guacd_writer.close()
        listener.close()
