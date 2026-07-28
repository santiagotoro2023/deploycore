import asyncio
import codecs
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
from app.hypervisors import get_driver
from app.models.app_asset import AppAsset
from app.models.deployment import Deployment
from app.models.hypervisor import HypervisorHost
from app.models.managed_host import ManagedHost
from app.models.user import ROLE_ORDER, Role, User
from app.redis import get_redis
from app.schemas.deployment import PowerAction, PowerStateRead
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


async def _power_target_for_host(db: AsyncSession, host: ManagedHost) -> tuple[object, str] | None:
    """Resolve the hypervisor driver + VM moref for a managed host so its
    power can be read/changed exactly the way a Deployment's own power
    controls already work (see deployments.py). Only a host DeployCore
    itself deployed - one linked to a Deployment that still has a live VM -
    can be powered from here; a standalone (agent-only) host has no VM this
    app knows how to reach, so no power control is offered for it. Returns
    (driver, vm_moref), or None when there's no controllable VM."""
    if host.deployment_id is None:
        return None
    deployment = await db.get(Deployment, host.deployment_id)
    if deployment is None or deployment.vm_moref is None:
        return None
    hv_host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
    if hv_host is None:
        return None
    return get_driver(hv_host), deployment.vm_moref


async def _require_power_target(
    db: AsyncSession, org_id: uuid.UUID, host_id: uuid.UUID
) -> tuple[ManagedHost, object, str]:
    host = await _get_org_managed_host(db, org_id, host_id)
    target = await _power_target_for_host(db, host)
    if target is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "power control isn't available for this host - it isn't linked to a VM that DeployCore deployed",
        )
    driver, vm_moref = target
    return host, driver, vm_moref


@router.get(
    "/api/organizations/{org_id}/managed-hosts/{host_id}/power",
    response_model=PowerStateRead,
    dependencies=[Depends(require_role(Role.READONLY))],
)
async def get_managed_host_power(
    org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> PowerStateRead:
    """Current hypervisor power state, or a null power_state for a host with
    no controllable VM (standalone/agent-only) - the frontend hides the
    power menu entirely in that case rather than showing dead buttons."""
    host = await _get_org_managed_host(db, org_id, host_id)
    target = await _power_target_for_host(db, host)
    if target is None:
        return PowerStateRead(power_state=None)
    driver, vm_moref = target
    state = await driver.get_power_state(vm_moref)
    return PowerStateRead(power_state=state.value)


async def _run_power_action(
    db: AsyncSession, org_id: uuid.UUID, host_id: uuid.UUID, current_user: User, action: str, hard: bool = False
) -> PowerStateRead:
    """Shared body for the three power-change endpoints below - resolves the
    target, runs the action, audits it, and returns the resulting state.
    A hypervisor fault (resetting an already-off VM, VMware Tools missing
    for a graceful shutdown, etc.) is surfaced as a clean 502 with the
    hypervisor's own message instead of a bare 500 traceback."""
    host, driver, vm_moref = await _require_power_target(db, org_id, host_id)
    try:
        if action == "on":
            await driver.power_on(vm_moref)
        elif action == "off":
            await driver.power_off(vm_moref, hard=hard)
        elif action == "reset":
            await driver.reset(vm_moref)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - hypervisor faults reach the operator as a readable message
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"the hypervisor rejected the power action: {str(exc)[:200]}")
    audit.record(
        db, action=f"managed_host.power_{action}", target_type="managed_host", org_id=org_id,
        user_id=current_user.id, target_id=host.id,
        detail={"name": host.name, **({"hard": hard} if action == "off" else {})},
    )
    await db.commit()
    state = await driver.get_power_state(vm_moref)
    return PowerStateRead(power_state=state.value)


@router.post(
    "/api/organizations/{org_id}/managed-hosts/{host_id}/power/on",
    response_model=PowerStateRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def power_on_managed_host(
    org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PowerStateRead:
    return await _run_power_action(db, org_id, host_id, current_user, "on")


@router.post(
    "/api/organizations/{org_id}/managed-hosts/{host_id}/power/off",
    response_model=PowerStateRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def power_off_managed_host(
    org_id: uuid.UUID, host_id: uuid.UUID, body: PowerAction, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PowerStateRead:
    """hard=false is a graceful guest shutdown (needs VMware Tools/the guest
    to honor it); hard=true is an immediate power off, matching the ESXi
    console's "Shut down guest" vs "Power off" distinction."""
    return await _run_power_action(db, org_id, host_id, current_user, "off", hard=body.hard)


@router.post(
    "/api/organizations/{org_id}/managed-hosts/{host_id}/power/reset",
    response_model=PowerStateRead,
    dependencies=[Depends(require_role(Role.OPERATOR))],
)
async def reset_managed_host(
    org_id: uuid.UUID, host_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PowerStateRead:
    return await _run_power_action(db, org_id, host_id, current_user, "reset")


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

    # Defensive int parse: a malformed w/h must never raise here and kill the
    # route before it can send a readable close - fall back to the defaults
    # instead. (guacamole-common-js historically appended a stray "?undefined"
    # to the query string when connect() was called with no data, which turned
    # h into e.g. "800?undefined"; the frontend now passes params the correct
    # way, but this stays as a belt-and-suspenders guard.)
    try:
        width = int(websocket.query_params.get("w", _DEFAULT_CONNECT_WIDTH))
        height = int(websocket.query_params.get("h", _DEFAULT_CONNECT_HEIGHT))
    except (TypeError, ValueError):
        width, height = _DEFAULT_CONNECT_WIDTH, _DEFAULT_CONNECT_HEIGHT
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
    close event to ws.onclose in the browser instead.

    Connect mode's client (guacamole-common-js WebSocketTunnel) opens the
    socket REQUESTING the "guacamole" subprotocol; per RFC 6455 / the WHATWG
    WebSocket spec, if the server's 101 response doesn't echo that
    subprotocol back, the browser MUST fail the connection immediately (this
    was an independent, fatal Connect-mode bug - the socket died right after
    the handshake, before a single byte flowed). So the accepted subprotocol
    is chosen from what the client actually offered: "guacamole" when present
    (Connect), nothing when absent (Shadow's plain WebSocket offers none, and
    replying with an unoffered subprotocol makes IT fail instead). Read from
    the upgrade header directly, before the mode query param is even parsed,
    since the two always agree by construction."""
    offered_subprotocols = websocket.headers.get("sec-websocket-protocol", "")
    subprotocol = "guacamole" if "guacamole" in offered_subprotocols else None
    await websocket.accept(subprotocol=subprotocol)

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
    # Fail fast, with a message the operator can act on, rather than handing
    # guacd empty credentials. Since the protocol version is now negotiated
    # properly (1.5.0), guacd answers missing credentials with a "required"
    # instruction and WAITS for an argv reply instead of erroring - which,
    # unanswered, looks exactly like a dead session until the browser's own
    # 15s receive timeout fires as "Server timeout". Better to say what's
    # actually wrong.
    if not rdp_username or not rdp_password:
        await websocket.close(
            code=4501,
            reason="This host has no saved RDP username/password - set them with the pencil (edit) button.",
        )
        return

    session_id_bytes = bytes.fromhex(session_id_hex)

    async def _bridge_listener_to_agent(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """guacd connected to our ephemeral listener below - pump its bytes
        to/from the agent's tunneled RDP stream (binary control-channel
        frames tagged with this session's id)."""
        remote_session.register_tunnel_writer(session_id_hex, writer)
        byte_count = 0
        logger.info("Connect-mode session %s: guacd connected to the ephemeral listener.", session_id_hex)
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                byte_count += len(chunk)
                await remote_session.send_bytes_to_agent(agent, session_id_bytes + chunk)
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            # Confirms whether ANY RDP bytes ever made it from guacd toward
            # the agent for this session - distinct from whether the agent's
            # own reply bytes come back (see ConnectTunnel's matching log on
            # the agent side) - a session stuck at "Establishing a secure
            # session" needs to be able to tell which of the two directions,
            # if either, is actually moving.
            logger.info("Connect-mode session %s: guacd->agent leg ended after %d bytes.", session_id_hex, byte_count)
            remote_session.unregister_tunnel_writer(session_id_hex)
            writer.close()

    guacd_dialed_back = asyncio.Event()

    def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        guacd_dialed_back.set()
        asyncio.create_task(_bridge_listener_to_agent(reader, writer))

    listener = await asyncio.start_server(_handle_client, host="0.0.0.0", port=0)
    ephemeral_port = listener.sockets[0].getsockname()[1]

    try:
        guacd_reader, guacd_writer, ready_instruction, tunnel_host = await remote_session.open_guacd_connection(
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
        # The Guacamole protocol over a browser WebSocket is a TEXT protocol:
        # guacamole-common-js's tunnel/parser do string operations on every
        # frame and have zero binary handling, so a binary frame arrives as a
        # Blob, throws in the parser, and silently closes the tunnel - the
        # session then hangs forever at "Establishing a secure session". Decode
        # guacd's raw bytes and send text frames instead. An incremental
        # decoder is required because a 65536-byte TCP read can split a
        # multi-byte UTF-8 sequence across a boundary; the parser itself
        # already buffers partial *instructions* across frames, but only for
        # valid strings.
        decoder = codecs.getincrementaldecoder("utf-8")()
        total = 0
        logged = 0
        try:
            while True:
                chunk = await guacd_reader.read(65536)
                if not chunk:
                    break
                text = decoder.decode(chunk)
                if text:
                    total += len(text)
                    # The browser's Guacamole.Client only reaches CONNECTED on
                    # the first "sync" instruction, so "which opcodes did guacd
                    # actually emit" is THE question when a session appears to
                    # hang. Log a bounded prefix of the real stream (opcodes
                    # only carry no credentials; an "error" instruction's own
                    # reason text is exactly what's wanted here).
                    if logged < 12:
                        logged += 1
                        logger.info(
                            "Connect-mode session %s: guacd -> browser [%d] %s",
                            session_id_hex, logged, text[:200].replace("\n", " "),
                        )
                    await websocket.send_text(text)
        except Exception:  # noqa: BLE001 - browser disconnected or guacd went away, either way stop forwarding
            pass
        finally:
            logger.info(
                "Connect-mode session %s: guacd->browser leg ended after %d chars.", session_id_hex, total,
            )

    # Replay guacd's "ready" (consumed by this backend's own handshake) so the
    # browser's tunnel flips from CONNECTING to OPEN on a real instruction.
    # NOTE, checked against guacamole-common-js 1.5.5's actual source rather
    # than assumed: Guacamole.Client has NO "ready" handler and silently drops
    # the opcode - the ONLY thing that moves the CLIENT to STATE_CONNECTED is
    # the first "sync" instruction, which guacd emits once the RDP session is
    # genuinely up. So this is tunnel-state hygiene, not what makes a session
    # connect: a session still stuck on the spinner means no "sync" arrived,
    # i.e. the RDP leg never came up (see the guacd->browser logging below).
    await websocket.send_text(ready_instruction)

    # Start forwarding guacd's output BEFORE waiting on the dial-back. guacd
    # reports its own failures as an "error" instruction on this stream, and
    # waiting first meant anything it said during that window was thrown away -
    # turning a specific, actionable reason ("Connection refused", a security
    # negotiation failure) into a generic "couldn't reach the tunnel". Now
    # whatever guacd says reaches both the log and the browser.
    forward_task = asyncio.create_task(_from_guacd_to_browser())

    # guacd must now dial the per-session listener above. If it never does, the
    # session would otherwise just sit there until the browser's own 15s
    # receive timeout reports a bare "Server timeout" with no cause.
    try:
        await asyncio.wait_for(guacd_dialed_back.wait(), timeout=10)
        logger.info("Connect-mode session %s: guacd dialed the session tunnel.", session_id_hex)
    except asyncio.TimeoutError:
        logger.warning(
            "Connect-mode session %s: guacd never connected to the session tunnel at %s:%d. "
            "guacd's own output for this session is logged above, if it said anything.",
            session_id_hex, tunnel_host, ephemeral_port,
        )
        forward_task.cancel()
        guacd_writer.close()
        listener.close()
        await websocket.close(
            code=4504,
            reason="The remote desktop daemon could not reach this session's tunnel - check the api and guacd containers.",
        )
        return
    try:
        while True:
            raw = await websocket.receive()
            if raw["type"] == "websocket.disconnect":
                break
            if (text := raw.get("text")) is not None:
                # guacamole-common-js sends an internal keepalive ping every
                # ~2s as an empty-opcode instruction ("0.,4.ping,...;") and
                # expects the TUNNEL ENDPOINT to echo it back - guacd itself
                # never answers it. Forwarding it into guacd (as this used to)
                # means a quiet-but-healthy session - e.g. during a slow
                # NLA/CredSSP handshake - gets no reply within the client's 15s
                # receive timeout and is killed with "Server timeout". Echo the
                # ping here instead; everything else passes through to guacd.
                if text.startswith("0.,"):
                    await websocket.send_text(text)
                else:
                    guacd_writer.write(text.encode())
                    await guacd_writer.drain()
            elif (data := raw.get("bytes")) is not None:
                guacd_writer.write(data)
                await guacd_writer.drain()
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        guacd_writer.close()
        listener.close()
