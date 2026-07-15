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

from app.config import get_settings

logger = logging.getLogger(__name__)

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
    if resp.status_code != 200:
        raise RemoteDesktopError(f"RustDesk admin login failed (HTTP {resp.status_code}).")
    token = resp.json().get("token")
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


async def create_session_url(rustdesk_id: str, rustdesk_password: str, host_name: str) -> str:
    """Ensures the peer is known to the service account's address book, mints
    a one-time expiring share link for it, and returns the embeddable web-client
    URL. rustdesk_password is the host's own permanent/unattended password,
    decrypted from ManagedHost.rustdesk_key by the caller."""
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
        if ab_resp.status_code != 200:
            logger.info("address_book/create for %s returned HTTP %s (may already exist)", rustdesk_id, ab_resp.status_code)
    except RemoteDesktopError:
        raise
    except Exception as exc:  # noqa: BLE001 - network hiccup on the best-effort step, share step still tried
        logger.warning("address_book/create for %s failed: %s", rustdesk_id, exc)

    share_resp = await _admin_post(
        "address_book/shareByWebClient",
        {"id": rustdesk_id, "password_type": "fixed", "password": rustdesk_password, "expire": _SHARE_EXPIRE_SECONDS},
    )
    if share_resp.status_code != 200:
        raise RemoteDesktopError(f"Could not start a remote session (HTTP {share_resp.status_code}).")
    share_token = share_resp.json().get("share_token")
    if not share_token:
        raise RemoteDesktopError("Remote session link was not issued by the RustDesk server.")

    public = get_settings().rustdesk_api_public_url.rstrip("/")
    return f"{public}/webclient2/#/?share_token={share_token}"
