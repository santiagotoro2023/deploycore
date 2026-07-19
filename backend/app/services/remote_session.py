"""In-process signaling/relay for the native Remote Management agent (see
remote-agent/PROTOCOL.md). No RustDesk, no external relay/rendezvous server -
the agent's persistent control WebSocket and an operator's session WebSocket
are both held by THIS process and bridged directly in memory:

  - Shadow mode: this module is a dumb relay for the WebRTC SDP/ICE
    handshake only. Once that completes, video and input flow directly
    between the browser and the agent (WebRTC, LAN-direct or via coturn) -
    this process is never in that data path at all.
  - Connect mode: this module speaks guacd's own wire protocol (the
    select/args/connect handshake below) on the operator's behalf - the
    browser only ever sees raw Guacamole-protocol bytes over its
    WebSocket, guacamole-common-js on its own - and bridges guacd's own
    outbound RDP connection back to the agent's tunneled bytes.

ponytail: no cross-worker pub/sub (Redis, etc.) for the agent/session
registries below - this api container runs as a single uvicorn process (see
docker-compose.yml's own command), so there's only ever one process holding
both sockets. Add a real pub/sub bridge only if this ever actually runs
multi-worker - a single in-process dict is the whole problem today.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.setting import Setting, SettingScope

logger = logging.getLogger(__name__)

REMOTE_HOST_SETTING_KEY = "remote_management_host"
APP_PUBLIC_URL_SETTING_KEY = "app_public_url_override"


async def resolve_public_host(db: AsyncSession) -> str:
    result = await db.execute(
        select(Setting.value).where(Setting.scope == SettingScope.GLOBAL, Setting.key == REMOTE_HOST_SETTING_KEY)
    )
    value = result.scalar_one_or_none()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return get_settings().turn_host


async def resolve_app_public_url(db: AsyncSession) -> str:
    result = await db.execute(
        select(Setting.value).where(Setting.scope == SettingScope.GLOBAL, Setting.key == APP_PUBLIC_URL_SETTING_KEY)
    )
    value = result.scalar_one_or_none()
    if isinstance(value, str) and value.strip():
        return value.strip().rstrip("/")
    return get_settings().app_public_url.rstrip("/")


# Only meaningful for a managed host that ISN'T on this server's own LAN -
# ICE always tries a direct path first (see PROTOCOL.md), so a same-LAN
# install (the common case this app is built for) needs none of this
# forwarded at all. guacd needs no port of its own - only the agent and this
# api container ever talk to it, over the internal compose network.
RELAY_PORTS = [
    {"port": 3478, "proto": "TCP+UDP", "purpose": "STUN/TURN (coturn) - only for a host that isn't on this server's own LAN"},
    {"port": 49160, "proto": "UDP", "purpose": "TURN relay range start (49160-49200) - same non-LAN case"},
]


class RemoteSessionError(Exception):
    """Surfaced to the operator as a plain message, never a raw traceback."""


async def probe() -> tuple[bool, bool, str | None]:
    """Cheap health check for the setup banner: (configured, reachable, detail).
    configured = a TURN password is set; reachable = guacd's own port
    answered a real TCP connect. Never raises."""
    settings = get_settings()
    if not settings.turn_password:
        return False, False, "No TURN password is set yet."
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(settings.guacd_host, settings.guacd_port), timeout=3
        )
        writer.close()
        await writer.wait_closed()
        return True, True, None
    except Exception as exc:  # noqa: BLE001 - network hiccup / guacd still starting
        return True, False, f"Could not reach the Remote Management daemon (guacd): {exc}"


# --- Agent control-channel + operator session registries (in-process) ---


@dataclass
class SessionConnection:
    session_id: str
    host_id: uuid.UUID
    mode: str  # "shadow" | "connect"
    websocket: WebSocket


@dataclass
class AgentConnection:
    host_id: uuid.UUID
    websocket: WebSocket
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_AGENTS: dict[uuid.UUID, AgentConnection] = {}
_SESSIONS: dict[str, SessionConnection] = {}
# Connect mode only - the local accepted-socket writer bridging guacd's own
# outbound RDP connection to this session's tunneled bytes (see
# managed_hosts.py's connect-mode route). Separate from _SESSIONS: Shadow's
# `signal` JSON never touches this, and Connect's binary tunnel bytes never
# touch a browser WebSocket directly - two different peers, kept distinct
# rather than overloading one registry for both.
_TUNNEL_WRITERS: dict[str, asyncio.StreamWriter] = {}


def register_agent(host_id: uuid.UUID, websocket: WebSocket) -> AgentConnection:
    conn = AgentConnection(host_id=host_id, websocket=websocket)
    _AGENTS[host_id] = conn
    return conn


def unregister_agent(host_id: uuid.UUID) -> None:
    _AGENTS.pop(host_id, None)


def get_agent(host_id: uuid.UUID) -> AgentConnection | None:
    return _AGENTS.get(host_id)


def register_session(session: SessionConnection) -> None:
    _SESSIONS[session.session_id] = session


def unregister_session(session_id: str) -> None:
    _SESSIONS.pop(session_id, None)


def get_session(session_id: str) -> SessionConnection | None:
    return _SESSIONS.get(session_id)


async def send_to_agent(agent: AgentConnection, message: dict) -> None:
    async with agent.send_lock:
        await agent.websocket.send_json(message)


async def send_bytes_to_agent(agent: AgentConnection, framed: bytes) -> None:
    async with agent.send_lock:
        await agent.websocket.send_bytes(framed)


def register_tunnel_writer(session_id_hex: str, writer: asyncio.StreamWriter) -> None:
    _TUNNEL_WRITERS[session_id_hex] = writer


def unregister_tunnel_writer(session_id_hex: str) -> None:
    _TUNNEL_WRITERS.pop(session_id_hex, None)


def get_tunnel_writer(session_id_hex: str) -> asyncio.StreamWriter | None:
    return _TUNNEL_WRITERS.get(session_id_hex)


# --- guacd wire protocol (Connect mode only) ---
#
# Each instruction is a comma-separated list of length-prefixed strings,
# terminated by ';' - e.g. select "rdp" is the 6-byte string "select"
# followed by the 3-byte string "rdp": `6.select,3.rdp;`. Hand-written
# rather than a dependency because this is genuinely small and fixed (this
# is the entire protocol surface guacamole-lite also hand-rolls, in JS, at
# about this size) - not something that warrants a library of its own.

_GUACD_CONNECT_TIMEOUT_SECONDS = 5


def _encode_instruction(opcode: str, *args: str) -> bytes:
    parts = [opcode, *args]
    encoded = ",".join(f"{len(p)}.{p}" for p in parts) + ";"
    return encoded.encode()


async def _read_instruction(reader: asyncio.StreamReader) -> list[str]:
    """Reads exactly one instruction and returns its parts (opcode first)."""
    args: list[str] = []
    while True:
        length_str = b""
        while True:
            ch = await reader.readexactly(1)
            if ch == b".":
                break
            length_str += ch
        length = int(length_str)
        value = (await reader.readexactly(length)).decode()
        terminator = await reader.readexactly(1)  # ',' between args, ';' at instruction end
        args.append(value)
        if terminator == b";":
            return args


async def open_guacd_connection(
    *, host: str, port: int, username: str | None, password: str | None, width: int, height: int, dpi: int = 96,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Does the select/args/connect/ready handshake with guacd on the
    operator's behalf - the browser (guacamole-common-js) never sees RDP
    credentials or connection parameters at all, only the raw protocol
    stream from this point on. `host`/`port` is the per-session local
    listener bridging to the agent's tunnel (see managed_hosts.py's
    connect-mode route), never the real target's own address - guacd has no
    idea it's dialing a tunnel rather than a directly-reachable host, which
    is exactly the point (see PROTOCOL.md)."""
    settings = get_settings()
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(settings.guacd_host, settings.guacd_port), timeout=_GUACD_CONNECT_TIMEOUT_SECONDS
    )
    writer.write(_encode_instruction("select", "rdp"))
    await writer.drain()

    args_instruction = await asyncio.wait_for(_read_instruction(reader), timeout=_GUACD_CONNECT_TIMEOUT_SECONDS)
    arg_names = args_instruction[1:]  # args_instruction[0] == "args"

    values = {
        "hostname": host,
        "port": str(port),
        "username": username or "",
        "password": password or "",
        "width": str(width),
        "height": str(height),
        "dpi": str(dpi),
        # Real, exact, live resizing via RDP's own Display Control channel -
        # the whole reason Connect mode needs no IDD driver, unlike Shadow.
        "resize-method": "display-update",
        "ignore-cert": "true",  # internal network, target's own self-signed RDP cert
        "enable-drive": "false",
        # NOT "enable-audio" (that key was simply wrong - confirmed against
        # guacd's own source, src/protocols/rdp/settings.c's GUAC_RDP_CLIENT_ARGS,
        # after a real Connect-mode failure on the first live test): the real
        # arg is "disable-audio", inverted sense. Left unset before this fix,
        # every OTHER guacd arg this dict doesn't name (security, domain,
        # server-layout, etc.) silently fell back to "" via connect_args'
        # own dict.get(name, "") default, which guacd/FreeRDP treat as "use
        # the built-in default" for nearly everything - "security" is the
        # one worth setting explicitly rather than trusting that default,
        # since RDP security negotiation failing outright is a real, higher-
        # stakes way for a connection to fail than most other unset options.
        "disable-audio": "true",
        "security": "any",  # let FreeRDP negotiate the best mode the target actually supports
    }
    connect_args = [values.get(name, "") for name in arg_names]
    writer.write(_encode_instruction("connect", *connect_args))
    await writer.drain()

    ready = await asyncio.wait_for(_read_instruction(reader), timeout=_GUACD_CONNECT_TIMEOUT_SECONDS)
    if not ready or ready[0] != "ready":
        writer.close()
        # ready[0] here is commonly "error" with guacd's own real reason as
        # ready[1] (its actual wire protocol, not guessed) - surfaced via
        # RemoteSessionError so it reaches the operator's browser (see
        # managed_hosts.py's own except block) instead of only ever showing
        # up in this container's own logs.
        raise RemoteSessionError(f"guacd did not accept the connection: {ready[1:] if len(ready) > 1 else ready!r}")

    # Confirms the select/args/connect handshake itself succeeded, distinct
    # from whether guacd's own outbound "RDP" connection (to the tunnel
    # below) then actually completes the RDP handshake - added specifically
    # so a session that hangs at "Establishing a secure session" has a way
    # to tell "guacd never even accepted this" from "it did, and got stuck
    # somewhere in the tunnel after."
    logger.info("guacd accepted the connect handshake (tunnel target %s:%d).", host, port)
    return reader, writer
