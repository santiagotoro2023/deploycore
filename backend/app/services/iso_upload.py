import hashlib
import logging
import shutil
import uuid
from pathlib import Path

from app.config import get_settings
from app.models.iso_asset import IsoKind
from app.services.iso_remaster import IsoRemasterError, remove_boot_prompt

logger = logging.getLogger(__name__)

# ponytail: sequential chunked upload (client sends chunks in order, server
# appends) rather than a full resumable-upload protocol with per-chunk
# offsets/retries, sufficient for an admin uploading a Windows ISO from the
# UI. Add offset-addressed chunks if uploads ever need to resume mid-file.


def _temp_path(iso_id: uuid.UUID) -> Path:
    return Path(get_settings().iso_build_tmp) / f"upload-{iso_id}.part"


def append_chunk(iso_id: uuid.UUID, chunk: bytes) -> None:
    path = _temp_path(iso_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as fh:
        fh.write(chunk)


def finalize(iso_id: uuid.UUID, filename: str, kind: IsoKind) -> tuple[str, str, int]:
    """Moves the assembled temp upload into permanent ISO storage, silently
    patching out the "press any key to boot from CD or DVD" prompt on
    Windows install media along the way (see iso_remaster.py, this is what
    lets a deployment boot the ISO with zero interaction instead of relying
    on a synthetic keypress). Returns (storage_path, checksum_sha256,
    size_bytes) for the file as it will actually be deployed."""
    src = _temp_path(iso_id)
    dest_dir = Path(get_settings().iso_storage_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{iso_id}-{filename}"
    shutil.move(str(src), str(dest))

    if kind == IsoKind.WINDOWS_ISO:
        try:
            remove_boot_prompt(dest)
        except IsoRemasterError:
            logger.exception("iso_remaster: failed to patch %s, keeping the original image", dest)

    sha256 = hashlib.sha256()
    with open(dest, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            sha256.update(block)
    size_bytes = dest.stat().st_size
    return str(dest), sha256.hexdigest(), size_bytes
