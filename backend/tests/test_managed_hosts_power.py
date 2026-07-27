"""Tests for the managed-host power endpoints (ESXi-style power toolbar).
Uses a fake hypervisor driver so no real vSphere is touched - the point is
that the route resolves a host's linked deployment/VM and calls the right
driver method, and that a standalone (agent-only) host correctly has no power
control.
"""

import pytest

from app.api.routes import managed_hosts as managed_hosts_route
from app.hypervisors.base import PowerState
from app.models import Role
from app.models.managed_host import ManagedHost
from tests.conftest import (
    auth_headers,
    make_deployment,
    make_disk_layout,
    make_hypervisor_host,
    make_iso_asset,
    make_organization,
    make_template,
    make_user,
)


class _FakeDriver:
    def __init__(self, state: PowerState = PowerState.POWERED_ON):
        self._state = state
        self.calls: list[tuple] = []

    async def power_on(self, vm_ref):
        self.calls.append(("on", vm_ref))
        self._state = PowerState.POWERED_ON

    async def power_off(self, vm_ref, hard=False):
        self.calls.append(("off", vm_ref, hard))
        self._state = PowerState.POWERED_OFF

    async def reset(self, vm_ref):
        self.calls.append(("reset", vm_ref))

    async def get_power_state(self, vm_ref):
        return self._state


async def _make_deployment_linked_host(db_session):
    org = await make_organization(db_session)
    user = await make_user(db_session, org=org, org_role=Role.OPERATOR)
    hv = await make_hypervisor_host(db_session, org)
    layout = await make_disk_layout(db_session, org)
    iso = await make_iso_asset(db_session, org)
    template = await make_template(db_session, org, layout, iso)
    deployment = await make_deployment(db_session, org, template, hv, user)
    deployment.vm_moref = "vm-123"
    await db_session.commit()
    host = ManagedHost(org_id=org.id, deployment_id=deployment.id, name="linked-host", enrolled=True)
    db_session.add(host)
    await db_session.commit()
    await db_session.refresh(host)
    return org, user, host


@pytest.mark.asyncio
async def test_power_state_on_off_reset(test_client, db_session, monkeypatch):
    org, user, host = await _make_deployment_linked_host(db_session)
    fake = _FakeDriver(PowerState.POWERED_OFF)
    monkeypatch.setattr(managed_hosts_route, "get_driver", lambda _h: fake)
    headers = await auth_headers(user)
    base = f"/api/organizations/{org.id}/managed-hosts/{host.id}/power"

    r = await test_client.get(base, headers=headers)
    assert r.status_code == 200
    assert r.json()["power_state"] == "poweredOff"

    r = await test_client.post(f"{base}/on", headers=headers)
    assert r.status_code == 200
    assert r.json()["power_state"] == "poweredOn"
    assert ("on", "vm-123") in fake.calls

    r = await test_client.post(f"{base}/off", json={"hard": True}, headers=headers)
    assert r.status_code == 200
    assert ("off", "vm-123", True) in fake.calls

    r = await test_client.post(f"{base}/reset", headers=headers)
    assert r.status_code == 200
    assert ("reset", "vm-123") in fake.calls


@pytest.mark.asyncio
async def test_power_unavailable_for_standalone_host(test_client, db_session):
    org = await make_organization(db_session)
    user = await make_user(db_session, org=org, org_role=Role.OPERATOR)
    host = ManagedHost(org_id=org.id, deployment_id=None, name="standalone", enrolled=True)
    db_session.add(host)
    await db_session.commit()
    await db_session.refresh(host)
    headers = await auth_headers(user)
    base = f"/api/organizations/{org.id}/managed-hosts/{host.id}/power"

    # No linked VM -> GET reports null (frontend hides the menu), action 409s.
    r = await test_client.get(base, headers=headers)
    assert r.status_code == 200
    assert r.json()["power_state"] is None

    r = await test_client.post(f"{base}/on", headers=headers)
    assert r.status_code == 409
