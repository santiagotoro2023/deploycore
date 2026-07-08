import hashlib
import shutil
import uuid
from pathlib import Path

from app.config import get_settings

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


def finalize(iso_id: uuid.UUID, filename: str) -> tuple[str, str, int]:
    """Moves the assembled temp upload into permanent ISO storage. Returns
    (storage_path, checksum_sha256, size_bytes)."""
    src = _temp_path(iso_id)
    dest_dir = Path(get_settings().iso_storage_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{iso_id}-{filename}"

    sha256 = hashlib.sha256()
    with open(src, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            sha256.update(block)
    size_bytes = src.stat().st_size

    shutil.move(str(src), str(dest))
    return str(dest), sha256.hexdigest(), size_bytes
