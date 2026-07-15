"""Talks to the self-hosted rustdesk-api server (see docker-compose.yml's
`rustdesk` service and remote-agent/README.md) to turn one enrolled
ManagedHost into a short-lived, login-free web-client URL DeployCore's
frontend can drop into an iframe.

The whole flow, verified against rustdesk-api's own Go source (see the
Remote Management architecture memory for the trail):

  1. Log in once as the DeployCore service account (POST /api/admin/login) ->
     an `api-token` (a custom header, NOT Bearer, for every /api/admin/* call).
     Cached in-process until it stops working, then re-fetched.
  2. Make sure the host's RustDesk ID is in that account's address book, with
     the host's own permanent password on file (POST /api/admin/address_book/create).
  3. Mint a one-time, expiring share link for that peer
     (POST /api/admin/address_book/shareByWebClient) -> a `share_token` that
     redeems anonymously (POST /api/shared-peer needs no login), so the
     operator's browser never needs its own rustdesk-api account.
  4. Hand back <public_url>/webclient2/#/?share_token=<token>.

ponytail: no persistent session store, no token-refresh scheduler - a single
module-level cached token re-fetched on the first 401/expiry is enough for a
low-frequency "operator clicked Connect" call. Revisit only if this ever gets
hammered.
"""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.setting import Setting, SettingScope

logger = logging.getLogger(__name__)

# Global setting the Settings UI writes; the api reads it live so agent-config,
# session links, and the readiness banner reflect a change immediately (the
# relay/ID containers themselves are realigned by the updater - see
# updater/update.sh apply_remote_management). Falls back to the env default,
# which scripts/setup.sh sets to this host's LAN IP, so a fresh install has
# working LAN Remote Management with nothing set.
REMOTE_HOST_SETTING_KEY = "remote_management_host"


async def resolve_public_host(db: AsyncSession) -> str:
    result = await db.execute(
        select(Setting.value).where(Setting.scope == SettingScope.GLOBAL, Setting.key == REMOTE_HOST_SETTING_KEY)
    )
    value = result.scalar_one_or_none()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return get_settings().rustdesk_relay_host


def public_url_for(host: str) -> str:
    """The browser loads the embedded web client from here (rustdesk-api's
    port 21114 on the public host)."""
    return f"http://{host}:21114"

_TIMEOUT_SECONDS = 15
_SHARE_EXPIRE_SECONDS = 60 * 60  # a connect session link good for an hour

# The ports a remote agent (which may be anywhere on the internet) needs to
# reach on this host. Surfaced to the setup banner so the user knows exactly
# what to forward/allow - the one part of setup that genuinely can't be
# automated from inside a container (router port-forwards, cloud firewall
# rules). Verified against the rustdesk-server-s6 image's exposed ports.
RELAY_PORTS = [
    {"port": 21115, "proto": "TCP", "purpose": "NAT type test"},
    {"port": 21116, "proto": "TCP+UDP", "purpose": "ID / rendezvous server (both TCP and UDP)"},
    {"port": 21117, "proto": "TCP", "purpose": "Relay server"},
    {"port": 21118, "proto": "TCP", "purpose": "Web client (ID over WebSocket)"},
    {"port": 21119, "proto": "TCP", "purpose": "Web client (relay over WebSocket)"},
    {"port": 21114, "proto": "TCP", "purpose": "Web client + API (the browser loads the session from here)"},
]

# Cached api-token from the last successful admin login. Cleared and re-fetched
# whenever a call comes back 401 (see _admin_post).
_api_token: str | None = None


class RemoteDesktopError(Exception):
    """Any failure reaching or being refused by the rustdesk-api server -
    surfaced to the operator as a plain message, never a raw traceback."""


def _unwrap(resp: httpx.Response, context: str) -> dict:
    """rustdesk-api wraps EVERY response as {"code": 0, "message": "success",
    "data": {...actual payload...}} - confirmed via a live call (a login
    response really does nest "token" under "data", not top-level, which is
    exactly the bug this replaces: both _login and create_session_url's
    share_token read used to look for the field at the wrong level). code 0
    means success regardless of HTTP status; the real payload is always under
    "data", never at the top."""
    try:
        body = resp.json()
    except ValueError:
        raise RemoteDesktopError(f"{context}: non-JSON response (HTTP {resp.status_code})")
    if resp.status_code != 200:
        raise RemoteDesktopError(f"{context} failed (HTTP {resp.status_code}): {body.get('message', '')}".rstrip(": "))
    code = body.get("code")
    if isinstance(code, int) and code != 0:
        raise RemoteDesktopError(f"{context} failed: {body.get('message') or f'code {code}'}")
    data = body.get("data")
    return data if isinstance(data, dict) else {}


async def probe() -> tuple[bool, bool, str | None]:
    """Cheap health check for the setup banner: (configured, reachable, detail).
    `configured` = the admin password is set at all; `reachable` = a real login
    against the rustdesk-api server just succeeded. Never raises - any failure
    becomes reachable=False with a human-readable detail."""
    settings = get_settings()
    if not settings.rustdesk_admin_password:
        return False, False, "No RustDesk admin password is set yet."
    try:
        await _login()
        return True, True, None
    except RemoteDesktopError as exc:
        return True, False, str(exc)
    except Exception as exc:  # noqa: BLE001 - network/DNS/connection failure to the rustdesk container
        return True, False, f"Could not reach the Remote Management server: {exc}"


async def _login() -> str:
    settings = get_settings()
    if not settings.rustdesk_admin_password:
        raise RemoteDesktopError("Remote Management isn't configured yet (no RustDesk admin password set).")
    url = f"{settings.rustdesk_api_internal_url.rstrip('/')}/api/admin/login"
    payload = {"username": settings.rustdesk_admin_username, "password": settings.rustdesk_admin_password}
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, json=payload)
    token = _unwrap(resp, "RustDesk admin login").get("token")
    if not token:
        raise RemoteDesktopError("RustDesk admin login returned no token.")
    return token


async def _admin_post(path: str, body: dict) -> httpx.Response:
    """POST to /api/admin/<path> with the cached api-token, logging in first
    if there isn't one and retrying exactly once on a 401 (token expired or
    was revoked server-side)."""
    global _api_token
    settings = get_settings()
    url = f"{settings.rustdesk_api_internal_url.rstrip('/')}/api/admin/{path.lstrip('/')}"
    for attempt in range(2):
        if _api_token is None:
            _api_token = await _login()
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, json=body, headers={"api-token": _api_token})
        if resp.status_code == 401 and attempt == 0:
            _api_token = None  # force a fresh login on the retry
            continue
        return resp
    return resp  # type: ignore[return-value]  # loop always assigns resp before here


async def create_session_url(rustdesk_id: str, rustdesk_password: str, host_name: str, public_url: str) -> str:
    """Ensures the peer is known to the service account's address book, mints
    a one-time expiring share link for it, and returns the embeddable web-client
    URL. rustdesk_password is the host's own permanent/unattended password,
    decrypted from ManagedHost.rustdesk_key by the caller. public_url is the
    browser-facing base URL (resolve_public_host + public_url_for), so the
    session honours the Settings-configured Remote Management host."""
    # Idempotent by intent: re-adding a peer already in the address book is a
    # harmless no-op / update, and this is the only place the current password
    # gets refreshed onto the rustdesk-api side, so it runs every session
    # rather than only on first connect. A non-200 here isn't necessarily
    # fatal (the peer may already be present), so it's logged, not raised -
    # the share step below is the real gate.
    try:
        ab_resp = await _admin_post(
            "address_book/create",
            {"id": rustdesk_id, "password": rustdesk_password, "alias": host_name},
        )
        _unwrap(ab_resp, "address_book/create")  # only for its own logging below on failure
    except RemoteDesktopError as exc:
        logger.info("address_book/create for %s: %s (may already exist, not fatal)", rustdesk_id, exc)
    except Exception as exc:  # noqa: BLE001 - network hiccup on the best-effort step, share step still tried
        logger.warning("address_book/create for %s failed: %s", rustdesk_id, exc)

    share_resp = await _admin_post(
        "address_book/shareByWebClient",
        {"id": rustdesk_id, "password_type": "fixed", "password": rustdesk_password, "expire": _SHARE_EXPIRE_SECONDS},
    )
    share_token = _unwrap(share_resp, "Could not start a remote session").get("share_token")
    if not share_token:
        raise RemoteDesktopError("Remote session link was not issued by the RustDesk server.")

    return f"{public_url.rstrip('/')}/webclient2/#/?share_token={share_token}"
