from app.models.user import Role
from app.services import audit

from tests.conftest import auth_headers, make_organization, make_user


async def test_audit_log_is_scoped_to_the_caller_s_organization(test_client, db_session):
    org_a = await make_organization(db_session)
    org_b = await make_organization(db_session)

    audit.record(db_session, action="hypervisor.create", target_type="hypervisor", org_id=org_a.id, detail={"name": "host-a"})
    audit.record(db_session, action="hypervisor.create", target_type="hypervisor", org_id=org_b.id, detail={"name": "host-b"})
    await db_session.commit()

    # a user with a role in org A only can read org A's log, and never sees org B's rows in it
    org_a_user = await make_user(db_session, org=org_a, org_role=Role.READONLY)
    headers = await auth_headers(org_a_user)
    r = await test_client.get(f"/api/organizations/{org_a.id}/audit-log", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["detail"]["name"] == "host-a"

    # that same user has no role at all in org B, so org B's log is forbidden outright
    r = await test_client.get(f"/api/organizations/{org_b.id}/audit-log", headers=headers)
    assert r.status_code == 403

    # a global admin can see either organization's log
    global_admin = await make_user(db_session, global_role=Role.ADMIN)
    admin_headers = await auth_headers(global_admin)
    r = await test_client.get(f"/api/organizations/{org_b.id}/audit-log", headers=admin_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["detail"]["name"] == "host-b"
