from app.models import Role
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


async def test_org_create_requires_admin(test_client, db_session):
    readonly = await make_user(db_session, global_role=Role.READONLY)
    operator = await make_user(db_session, global_role=Role.OPERATOR)
    admin = await make_user(db_session, global_role=Role.ADMIN)
    body = {"name": "Acme", "slug": "acme"}

    r = await test_client.post("/api/organizations", json=body, headers=auth_headers(readonly))
    assert r.status_code == 403
    r = await test_client.post("/api/organizations", json=body, headers=auth_headers(operator))
    assert r.status_code == 403
    r = await test_client.post("/api/organizations", json=body, headers=auth_headers(admin))
    assert r.status_code == 201


async def test_org_update_requires_admin(test_client, db_session):
    org = await make_organization(db_session)
    readonly = await make_user(db_session, org=org, org_role=Role.READONLY)
    operator = await make_user(db_session, org=org, org_role=Role.OPERATOR)
    admin = await make_user(db_session, org=org, org_role=Role.ADMIN)
    body = {"description": "updated"}

    r = await test_client.patch(f"/api/organizations/{org.id}", json=body, headers=auth_headers(readonly))
    assert r.status_code == 403
    r = await test_client.patch(f"/api/organizations/{org.id}", json=body, headers=auth_headers(operator))
    assert r.status_code == 403
    r = await test_client.patch(f"/api/organizations/{org.id}", json=body, headers=auth_headers(admin))
    assert r.status_code == 200


async def test_org_list_and_get_allow_readonly(test_client, db_session):
    org = await make_organization(db_session)
    readonly = await make_user(db_session, org=org, org_role=Role.READONLY)

    r = await test_client.get("/api/organizations", headers=auth_headers(readonly))
    assert r.status_code == 200
    r = await test_client.get(f"/api/organizations/{org.id}", headers=auth_headers(readonly))
    assert r.status_code == 200


async def test_user_create_requires_global_admin(test_client, db_session):
    org = await make_organization(db_session)
    org_admin = await make_user(db_session, org=org, org_role=Role.ADMIN)
    global_admin = await make_user(db_session, global_role=Role.ADMIN)
    body = {"email": "new@example.com", "password": "Passw0rd!", "display_name": "New User"}

    # an admin scoped to a single org is not a global admin: user management is global-only
    r = await test_client.post("/api/users", json=body, headers=auth_headers(org_admin))
    assert r.status_code == 403
    r = await test_client.post("/api/users", json=body, headers=auth_headers(global_admin))
    assert r.status_code == 201


async def test_assign_org_role_requires_global_admin(test_client, db_session):
    org = await make_organization(db_session)
    target = await make_user(db_session, global_role=Role.NONE)
    operator = await make_user(db_session, org=org, org_role=Role.OPERATOR)
    global_admin = await make_user(db_session, global_role=Role.ADMIN)
    body = {"org_id": str(org.id), "role": "operator"}

    r = await test_client.post(f"/api/users/{target.id}/org-roles", json=body, headers=auth_headers(operator))
    assert r.status_code == 403
    r = await test_client.post(f"/api/users/{target.id}/org-roles", json=body, headers=auth_headers(global_admin))
    assert r.status_code == 204


async def test_unauthenticated_request_is_rejected(test_client):
    r = await test_client.get("/api/organizations")
    assert r.status_code == 401


async def test_vm_lifecycle_actions_require_operator_or_admin(test_client, db_session):
    org = await make_organization(db_session)
    disk_layout = await make_disk_layout(db_session, org)
    iso_asset = await make_iso_asset(db_session, org)
    template = await make_template(db_session, org, disk_layout, iso_asset)
    host = await make_hypervisor_host(db_session, org)
    admin_user = await make_user(db_session, org=org, org_role=Role.ADMIN)
    deployment = await make_deployment(db_session, org, template, host, admin_user)

    readonly = await make_user(db_session, org=org, org_role=Role.READONLY)
    operator = await make_user(db_session, org=org, org_role=Role.OPERATOR)

    # readonly is rejected before ever touching the (nonexistent) hypervisor
    r = await test_client.post(
        f"/api/organizations/{org.id}/deployments/{deployment.id}/power/on", headers=auth_headers(readonly)
    )
    assert r.status_code == 403
    r = await test_client.delete(
        f"/api/organizations/{org.id}/deployments/{deployment.id}/vm", headers=auth_headers(readonly)
    )
    assert r.status_code == 403

    # deleting the VM is admin-only; an operator is rejected the same way, before any hypervisor call
    r = await test_client.delete(
        f"/api/organizations/{org.id}/deployments/{deployment.id}/vm", headers=auth_headers(operator)
    )
    assert r.status_code == 403


async def test_dashboard_overview_requires_global_admin(test_client, db_session):
    org = await make_organization(db_session)
    org_admin = await make_user(db_session, org=org, org_role=Role.ADMIN)
    global_admin = await make_user(db_session, global_role=Role.ADMIN)

    # an admin scoped to one org is not a global admin: the cross-org overview is global-only
    r = await test_client.get("/api/dashboard/overview", headers=auth_headers(org_admin))
    assert r.status_code == 403
    r = await test_client.get("/api/dashboard/overview", headers=auth_headers(global_admin))
    assert r.status_code == 200
