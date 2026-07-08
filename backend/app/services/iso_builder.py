import asyncio
import shutil
from pathlib import Path

from app.config import get_settings
from app.hypervisors.base import HypervisorDriver
from app.models.deployment import Deployment


async def build_and_upload_answer_iso(
    driver: HypervisorDriver, deployment: Deployment, rendered_xml: str
) -> str:
    """Writes the rendered autounattend.xml to a per-deployment temp dir
    (named by deployment id, so a crashed build is traceable), shells out to
    genisoimage, uploads the result to the hypervisor datastore, and always
    removes the local temp dir before returning or raising. The remote copy
    (which contains a plaintext local admin password) is the caller's
    responsibility to delete once the deployment finishes, success or fail.
    """
    temp_dir = Path(get_settings().iso_build_tmp) / str(deployment.id)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        (temp_dir / "autounattend.xml").write_text(rendered_xml, encoding="utf-8")
        iso_path = temp_dir / f"{deployment.id}-answer.iso"
        proc = await asyncio.create_subprocess_exec(
            "genisoimage",
            "-o",
            str(iso_path),
            "-J",
            "-R",
            str(temp_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"genisoimage failed: {stderr.decode(errors='replace')}")

        remote_name = f"{deployment.id}-answer.iso"
        return await driver.upload_iso_to_datastore(str(iso_path), remote_name)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
