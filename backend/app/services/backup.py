import asyncio
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

BACKUP_RETENTION_COUNT = 14


def _pg_dump_uri() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def perform_backup() -> Path:
    """Shells out to pg_dump (custom format, already compressed) and prunes
    anything beyond the newest BACKUP_RETENTION_COUNT files. Same
    never-shell-True posture as iso_builder.build_and_upload_answer_iso."""
    backup_dir = Path(get_settings().backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = backup_dir / f"deploycore-{timestamp}.dump"

    proc = await asyncio.create_subprocess_exec(
        "pg_dump",
        f"--dbname={_pg_dump_uri()}",
        "-Fc",
        "-f",
        str(dump_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {stderr.decode(errors='replace')}")

    existing = sorted(backup_dir.glob("deploycore-*.dump"), key=lambda p: p.name, reverse=True)
    for stale in existing[BACKUP_RETENTION_COUNT:]:
        stale.unlink(missing_ok=True)

    return dump_path
