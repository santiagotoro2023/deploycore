from app.models.user import Role

from tests.conftest import auth_headers, make_organization, make_user


async def test_global_admin_can_create_upload_and_delete_a_global_iso(test_client, db_session):
    global_admin = await make_user(db_session, global_role=Role.ADMIN)
    org = await make_organization(db_session)
    headers = await auth_headers(global_admin)

    create = await test_client.post(
        "/api/iso-assets/global", json={"filename": "win2025.iso", "kind": "windows_iso"}, headers=headers
    )
    assert create.status_code == 201
    iso = create.json()
    assert iso["org_id"] is None

    chunk = await test_client.post(
        f"/api/iso-assets/global/{iso['id']}/chunk", headers=headers, content=b"fake-iso-bytes"
    )
    assert chunk.status_code == 204

    finalize = await test_client.post(f"/api/iso-assets/global/{iso['id']}/finalize", headers=headers)
    assert finalize.status_code == 200
    assert finalize.json()["upload_status"] == "complete"

    # a global ISO is visible from every organization's list, not just its own
    listing = await test_client.get(f"/api/organizations/{org.id}/iso-assets", headers=headers)
    assert listing.status_code == 200
    assert any(row["id"] == iso["id"] for row in listing.json())

    delete = await test_client.delete(f"/api/iso-assets/global/{iso['id']}", headers=headers)
    assert delete.status_code == 204

    listing_after = await test_client.get(f"/api/organizations/{org.id}/iso-assets", headers=headers)
    assert not any(row["id"] == iso["id"] for row in listing_after.json())


async def test_org_scoped_admin_cannot_create_a_global_iso(test_client, db_session):
    org = await make_organization(db_session)
    org_admin = await make_user(db_session, org=org, org_role=Role.ADMIN)
    headers = await auth_headers(org_admin)

    create = await test_client.post(
        "/api/iso-assets/global", json={"filename": "win2025.iso", "kind": "windows_iso"}, headers=headers
    )
    assert create.status_code == 403
