"""Best-effort: pull the built agent .msi from its GitHub release and register
it as the global "Remote Agent" App Asset, so the Remote Management download
button and auto-install-on-deploy work with no manual upload (see
config.remote_agent_msi_url and .github/workflows/build-agent-msi.yml).

Runs on api startup (main.py lifespan). Idempotent and never fatal: if the
asset is already seeded it does nothing; if the release doesn't exist yet
(before CI's first successful build) or there's no internet, it logs and gives
up until the next restart. The one-liner install path and a manual upload both
work regardless, so this failing just means the in-app .msi button 404s until a
later restart succeeds.
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


async def _already_seeded(db: AsyncSession) -> bool:
    result = await db.execute(select(AppAsset).where(AppAsset.is_remote_agent.is_(True)))
    asset = result.scalars().first()
    # A row that points at a file that actually exists - a bare row whose file
    # went missing (volume reset, manual delete) should re-seed, not block.
    return asset is not None and bool(asset.storage_path) and Path(asset.storage_path).exists()


async def ensure_agent_asset_seeded(db: AsyncSession) -> None:
    settings = get_settings()
    url = settings.remote_agent_msi_url
    if not url:
        return
    if await _already_seeded(db):
        return

    logger.info("Fetching Remote Management agent installer from %s", url)
    try:
        async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            logger.info("Agent installer not available yet (HTTP %s) - will retry on next startup", resp.status_code)
            return
        data = resp.content
    except Exception as exc:  # noqa: BLE001 - no internet / DNS / release not published yet; best-effort
        logger.info("Could not fetch agent installer (%s) - will retry on next startup", exc)
        return

    dest_dir = Path(settings.app_asset_storage_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"remote-agent-{_FILENAME}"
    dest.write_bytes(data)

    # Reuse an existing is_remote_agent row (its file was missing) rather than
    # leaving a duplicate, otherwise create a fresh global asset.
    result = await db.execute(select(AppAsset).where(AppAsset.is_remote_agent.is_(True)))
    asset = result.scalars().first()
    if asset is None:
        asset = AppAsset(org_id=None, is_remote_agent=True)
        db.add(asset)
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
