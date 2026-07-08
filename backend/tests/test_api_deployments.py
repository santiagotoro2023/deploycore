from app.models import Role
from tests.conftest import (
    auth_headers,
    make_disk_layout,
    make_hypervisor_host,
    make_iso_asset,
    make_organization,
    make_template,
    make_user,
)


async def _org_with_template_and_host(db_session):
    org = await make_organization(db_session)
    disk_layout = await make_disk_layout(db_session, org)
    iso_asset = await make_iso_asset(db_session, org)
    template = await make_template(db_session, org, disk_layout, iso_asset)
    host = await make_hypervisor_host(db_session, org)
    return org, template, host


async def test_create_deployment_requires_operator(test_client, db_session):
    org, template, host = await _org_with_template_and_host(db_session)
    readonly = await make_user(db_session, org=org, org_role=Role.READONLY)
    operator = await make_user(db_session, org=org, org_role=Role.OPERATOR)
    body = {"template_id": str(template.id), "hypervisor_host_id": str(host.id), "hostname": "WIN-TEST01"}

    r = await test_client.post(f"/api/organizations/{org.id}/deployments", json=body, headers=auth_headers(readonly))
    assert r.status_code == 403

    r = await test_client.post(f"/api/organizations/{org.id}/deployments", json=body, headers=auth_headers(operator))
    assert r.status_code == 201
    data = r.json()
    assert data["hostname"] == "WIN-TEST01"
    assert data["state"] == "pending"


async def test_deployments_are_scoped_to_their_organization(test_client, db_session):
    org_a, template_a, host_a = await _org_with_template_and_host(db_session)
    org_b = await make_organization(db_session)
    operator_a = await make_user(db_session, org=org_a, org_role=Role.OPERATOR)
    readonly_b = await make_user(db_session, org=org_b, org_role=Role.READONLY)

    body = {"template_id": str(template_a.id), "hypervisor_host_id": str(host_a.id), "hostname": "WIN-TEST02"}
    created = await test_client.post(
        f"/api/organizations/{org_a.id}/deployments", json=body, headers=auth_headers(operator_a)
    )
    deployment_id = created.json()["id"]

    r = await test_client.get(f"/api/organizations/{org_a.id}/deployments/{deployment_id}", headers=auth_headers(operator_a))
    assert r.status_code == 200

    # readonly_b has no role in org_a, so this is rejected by RBAC before the org-scoped lookup even runs
    r = await test_client.get(f"/api/organizations/{org_a.id}/deployments/{deployment_id}", headers=auth_headers(readonly_b))
    assert r.status_code == 403
