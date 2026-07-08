from app.models import Role
from tests.conftest import auth_headers, make_organization, make_user


async def test_create_hypervisor_requires_admin(test_client, db_session):
    org = await make_organization(db_session)
    readonly = await make_user(db_session, org=org, org_role=Role.READONLY)
    operator = await make_user(db_session, org=org, org_role=Role.OPERATOR)
    admin = await make_user(db_session, org=org, org_role=Role.ADMIN)
    body = {
        "name": "lab-esxi-1",
        "type": "esxi",
        "api_endpoint": "esxi.lab.local",
        "username": "root",
        "credential": "hunter2",
    }

    r = await test_client.post(
        f"/api/organizations/{org.id}/hypervisors", json=body, headers=await auth_headers(readonly)
    )
    assert r.status_code == 403
    r = await test_client.post(
        f"/api/organizations/{org.id}/hypervisors", json=body, headers=await auth_headers(operator)
    )
    assert r.status_code == 403

    r = await test_client.post(
        f"/api/organizations/{org.id}/hypervisors", json=body, headers=await auth_headers(admin)
    )
    assert r.status_code == 201
    data = r.json()
    assert "credential" not in data
    assert "credential_encrypted" not in data
    assert data["name"] == "lab-esxi-1"


async def test_list_hypervisors_allows_readonly_and_scopes_by_org(test_client, db_session):
    org_a = await make_organization(db_session)
    org_b = await make_organization(db_session)
    admin_a = await make_user(db_session, org=org_a, org_role=Role.ADMIN)
    readonly_a = await make_user(db_session, org=org_a, org_role=Role.READONLY)
    readonly_b = await make_user(db_session, org=org_b, org_role=Role.READONLY)
    body = {
        "name": "lab-esxi-1",
        "type": "esxi",
        "api_endpoint": "esxi.lab.local",
        "username": "root",
        "credential": "hunter2",
    }
    await test_client.post(f"/api/organizations/{org_a.id}/hypervisors", json=body, headers=await auth_headers(admin_a))

    r = await test_client.get(f"/api/organizations/{org_a.id}/hypervisors", headers=await auth_headers(readonly_a))
    assert r.status_code == 200
    assert len(r.json()) == 1

    # readonly_b has no role in org_a, so org-scoped RBAC rejects the request outright
    r = await test_client.get(f"/api/organizations/{org_a.id}/hypervisors", headers=await auth_headers(readonly_b))
    assert r.status_code == 403
