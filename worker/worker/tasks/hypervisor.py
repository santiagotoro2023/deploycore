import uuid
from datetime import datetime, timezone

from app.db import SessionLocal
from app.hypervisors import get_driver
from app.models.hypervisor import ConnectionStatus, HypervisorHost


async def test_hypervisor_connection(ctx, host_id: str) -> dict:
    async with SessionLocal() as db:
        host = await db.get(HypervisorHost, uuid.UUID(host_id))
        if host is None:
            return {"ok": False, "message": "hypervisor host not found"}

        driver = get_driver(host)
        result = await driver.test_connection()

        host.last_test_status = ConnectionStatus.OK if result.ok else ConnectionStatus.FAILED
        host.last_test_at = datetime.now(timezone.utc)
        host.last_test_message = result.message
        await db.commit()
        return result.model_dump()
