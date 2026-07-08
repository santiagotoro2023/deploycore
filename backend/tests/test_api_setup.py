from tests.conftest import make_user


async def test_setup_status_reflects_whether_any_user_exists(test_client, db_session):
    r = await test_client.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["needs_setup"] is True

    await make_user(db_session)

    r = await test_client.get("/api/setup/status")
    assert r.json()["needs_setup"] is False


async def test_complete_setup_creates_admin_and_returns_a_working_token(test_client):
    body = {
        "instance_name": "Acme MSP",
        "admin_username": "bootstrap-admin",
        "admin_display_name": "Bootstrap Admin",
        "admin_password": "Passw0rd!",
    }
    r = await test_client.post("/api/setup", json=body)
    assert r.status_code == 201
    token = r.json()["access_token"]

    # the returned token must actually authenticate, this is exactly what
    # broke silently when create_access_token's signature changed to
    # require a session_id and setup.py wasn't updated to match: the user
    # was created and committed, but building the response then raised,
    # so the wizard reported "setup failed" for a setup that had actually
    # already happened.
    me = await test_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["user"]["username"] == "bootstrap-admin"


async def test_complete_setup_refuses_once_a_user_exists(test_client, db_session):
    await make_user(db_session)
    body = {
        "instance_name": "Acme MSP",
        "admin_username": "second-admin",
        "admin_display_name": "Second Admin",
        "admin_password": "Passw0rd!",
    }
    r = await test_client.post("/api/setup", json=body)
    assert r.status_code == 409
