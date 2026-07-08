import asyncio
import uuid
from pathlib import Path

import pytest

from app.config import get_settings
from app.services import iso_builder


class _FakeDriver:
    def __init__(self):
        self.uploaded_from = None

    async def upload_iso_to_datastore(self, local_path: str, remote_name: str) -> str:
        # capture whether the local ISO actually existed at upload time
        self.uploaded_from = local_path if Path(local_path).exists() else None
        return f"remote/{remote_name}"


class _FakeDeployment:
    def __init__(self):
        self.id = uuid.uuid4()


@pytest.fixture(autouse=True)
def _iso_build_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("ISO_BUILD_TMP", str(tmp_path))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_temp_dir_removed_after_successful_build(tmp_path):
    driver = _FakeDriver()
    deployment = _FakeDeployment()

    remote_path = await iso_builder.build_and_upload_answer_iso(driver, deployment, "<unattend></unattend>")

    assert remote_path == f"remote/{deployment.id}-answer.iso"
    assert driver.uploaded_from is not None  # the ISO existed locally when uploaded
    assert not (tmp_path / str(deployment.id)).exists()


async def test_temp_dir_removed_after_genisoimage_failure(tmp_path, monkeypatch):
    class _FakeProcess:
        returncode = 1

        async def communicate(self):
            return b"", b"mkisofs: command failed"

    async def _fake_exec(*args, **kwargs):
        return _FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    driver = _FakeDriver()
    deployment = _FakeDeployment()

    with pytest.raises(RuntimeError, match="genisoimage failed"):
        await iso_builder.build_and_upload_answer_iso(driver, deployment, "<unattend></unattend>")

    assert not (tmp_path / str(deployment.id)).exists()
