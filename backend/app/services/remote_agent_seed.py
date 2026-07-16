"""Best-effort: keep the built agent .msi (from its GitHub release) mirrored
as the global "Remote Agent" App Asset, so the Remote Management download
button and auto-install-on-deploy work with no manual upload (see
config.remote_agent_msi_url and .github/workflows/build-agent-msi.yml).

Runs on every api startup (main.py lifespan), and doesn't just seed once -
it checks whether a NEWER build is published and replaces the file in place
(same AppAsset row/id, so anything already referencing it - a template's
app_installs entry - keeps working with no re-attachment needed) if so.
Confirmed live this matters: a stale seeded .msi from an earlier CI build
silently kept getting used across several rounds of real agent-script fixes,
since the original version of this function only ever checked "does a file
already exist", never whether it was still current.

Never fatal: if the release doesn't exist yet, there's no internet, or a
check fails, it logs and gives up until the next restart. The one-liner
install path and a manual upload both work regardless.
"""

import hashlib
import logging
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.app_asset import AppAsset, AppKind
from app.models.iso_asset import UploadStatus

logger = logging.getLogger(__name__)

_FILENAME = "DeployCoreRemoteAgent.msi"
_DOWNLOAD_TIMEOUT_SECONDS = 120


async def _current_asset(db: AsyncSession) -> AppAsset | None:
    result = await db.execute(select(AppAsset).where(AppAsset.is_remote_agent.is_(True)))
    return result.scalars().first()


async def _remote_size(client: httpx.AsyncClient, url: str) -> int | None:
    """A cheap HEAD request's Content-Length, used as a freshness check before
    committing to a full download - two genuinely different CI builds (embed
    a rebuilt tray .exe, a different bundled RustDesk version, etc.) are
    exceedingly unlikely to happen to match byte-for-byte, so a size mismatch
    is a reliable enough signal that a newer build is available. Returns None
    on any failure (server doesn't support HEAD, network hiccup) - treated as
    "can't tell, fall back to re-downloading" by the caller."""
    try:
        resp = await client.head(url)
        if resp.status_code == 200:
            length = resp.headers.get("content-length")
            if length is not None:
                return int(length)
    except Exception:  # noqa: BLE001 - best-effort freshness check only
        pass
    return None


async def ensure_agent_asset_seeded(db: AsyncSession, *, force: bool = False) -> None:
    """force=True skips the cheap size-based freshness check entirely and
    always re-downloads. Confirmed live this matters: two genuinely different
    CI builds landed at the exact same byte size (a WiX version-string change
    that happened to not shift the MSI's total padded size at all) - the
    size-only check would have reported "already up to date" and silently
    kept serving the older, buggy build forever. The api-startup path still
    uses the cheap check (worth avoiding a 22MB re-download on every restart,
    and a rare false-negative there self-corrects on the next genuinely
    differently-sized build); the on-demand "Check for update" button
    (app_assets.py's refresh-agent route) passes force=True specifically so
    a user-triggered check is never subject to this false-negative at all."""
    settings = get_settings()
    url = settings.remote_agent_msi_url
    if not url:
        return

    existing = await _current_asset(db)
    existing_file_ok = existing is not None and bool(existing.storage_path) and Path(existing.storage_path).exists()

    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        if existing_file_ok and not force:
            remote_size = await _remote_size(client, url)
            if remote_size is not None and remote_size == existing.size_bytes:
                return  # already up to date, nothing to do
            logger.info(
                "A different Remote Management agent build is available (local %s bytes, remote %s bytes) - fetching it",
                existing.size_bytes, remote_size,
            )
        else:
            logger.info("Fetching Remote Management agent installer from %s", url)

        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.info("Agent installer not available (HTTP %s) - will retry on next startup", resp.status_code)
                return
            data = resp.content
        except Exception as exc:  # noqa: BLE001 - no internet / DNS / release not published yet; best-effort
            logger.info("Could not fetch agent installer (%s) - will retry on next startup", exc)
            return

    dest_dir = Path(settings.app_asset_storage_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"remote-agent-{_FILENAME}"
    dest.write_bytes(data)

    # Reuse the existing is_remote_agent row (same id) rather than creating a
    # new one - that's what lets a template's own app_installs entry (which
    # references this asset by id) keep working across an update with no
    # re-attachment, and avoids leaving a stale duplicate row behind.
    asset = existing if existing is not None else AppAsset(org_id=None, is_remote_agent=True)
    if existing is None:
        db.add(asset)
    old_path = asset.storage_path if existing is not None else None
    asset.kind = AppKind.MSI
    asset.name = "DeployCore Remote Management Agent"
    asset.filename = _FILENAME
    asset.storage_path = str(dest)
    asset.checksum_sha256 = hashlib.sha256(data).hexdigest()
    asset.size_bytes = len(data)
    # /qn makes the deployment pipeline's msiexec call silent (the MSI path in
    # winrm/client.install_app doesn't force it - it comes from install_args);
    # ALLUSERS=1 is added there, and SERVERURL/ENROLLTOKEN are injected
    # per-deployment (see provision.py).
    asset.default_install_args = "/qn"
    asset.upload_status = UploadStatus.COMPLETE
    await db.commit()
    logger.info("Seeded Remote Management agent installer (%d bytes) as the global Remote Agent asset", len(data))

    if old_path and old_path != str(dest):
        Path(old_path).unlink(missing_ok=True)
