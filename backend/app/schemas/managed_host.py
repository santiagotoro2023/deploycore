import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ManagedHostCreate(BaseModel):
    name: str
    deployment_id: uuid.UUID | None = None


class ManagedHostUpdate(BaseModel):
    name: str | None = None
    rdp_username: str | None = None
    # Omit entirely to leave the stored password unchanged (the frontend's
    # edit form never receives the current plaintext, so it can't send it
    # back by default - see ManagedHostRead.rdp_password_set); send "" to
    # explicitly clear it, any other value to replace it. Only fields set
    # in the request at all reach the model at all (route uses
    # exclude_unset=True), so a plain omitted field is a true no-op, not
    # accidentally cleared - "" is the only way to actually clear it.
    rdp_password: str | None = None


class ManagedHostRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    deployment_id: uuid.UUID | None
    name: str
    enrolled: bool
    # Only meaningful (and only actually needed by anyone) before
    # enrollment - the Remote Management page shows it as part of the
    # copyable install command for a standalone host that isn't attached
    # to any deployment (the deployed-via-template path never surfaces
    # this at all, provision.py injects it directly into install_args
    # instead, see the 0043 migration's own comment). Left visible after
    # enrollment too rather than special-cased away - same RBAC gate
    # either way, nothing sensitive enough to warrant hiding it.
    enroll_token: str
    rustdesk_id: str | None
    last_seen_at: datetime | None
    created_at: datetime
    rdp_username: str | None
    # NOT the plaintext password - just whether one is on file, so the edit
    # form can show "leave blank to keep the current password" vs "no
    # password set" without ever exposing it over a routine read. The
    # plaintext is only ever returned by the dedicated rdp-credentials route.
    rdp_password_set: bool


class RemotePort(BaseModel):
    port: int
    proto: str
    purpose: str


class RemoteStatus(BaseModel):
    """Drives the Remote Management setup banner. `configured` = the server-side
    secret is set; `reachable` = the rustdesk-api server answered a real login.
    relay_host/ports tell the user exactly what to forward/allow (the one setup
    step that can't be automated from inside a container). `app_public_url` is
    the SAME value provision.py injects as an agent's SERVERURL - surfaced here
    so the frontend's copy-paste install commands use this instance's one real
    address (config.app_public_url, normally auto-set by setup.sh from this
    host's LAN IP) instead of window.location.origin, which is whatever address
    the operator's own browser happens to be pointed at right now (a port
    forward, VPN, or otherwise) and is not guaranteed reachable from a target
    machine on the LAN - confirmed live as a real source of enrollment
    failures when the two diverged."""

    configured: bool
    reachable: bool
    detail: str | None
    relay_host: str
    ports: list[RemotePort]
    app_public_url: str


class ManagedHostSession(BaseModel):
    """A ready-to-embed, time-limited web-client URL for one connect session
    (see services/remote_desktop.py) - the frontend drops embed_url straight
    into an iframe."""

    embed_url: str


class ManagedHostRdpCredentials(BaseModel):
    """The plaintext RDP username/password, returned ONLY by the dedicated
    rdp-credentials route (never by the routine list/get routes - see
    ManagedHostRead.rdp_password_set). Fetched by the frontend right when
    "Connect" is clicked, to attempt auto-typing them into the remote
    session's own login screen (best-effort - see RemoteSession.tsx)."""

    username: str | None
    password: str | None


class RemoteAgentConfig(BaseModel):
    """Everything the agent installer needs to point a stock RustDesk client at
    this instance's self-hosted server - fetched by the installer using its
    enroll token, so nothing (relay address, server key) has to be copied by
    hand. See remote-agent/Install-DeployCoreAgent.ps1."""

    relay_host: str
    id_server: str
    relay_server: str
    key: str


class ManagedHostEnrollRequest(BaseModel):
    """Body of the agent's one-time enrollment call - both values are
    generated locally by the agent on first run (see ManagedHost's own
    docstring for why), never chosen by DeployCore."""

    rustdesk_id: str
    rustdesk_key: str
