from app.models.user import Role

from tests.conftest import auth_headers, make_user


async def test_upload_view_and_remove_own_avatar(test_client, db_session):
    user = await make_user(db_session, global_role=Role.ADMIN)
    headers = await auth_headers(user)

    me = await test_client.get("/api/auth/me", headers=headers)
    assert me.json()["user"]["has_avatar"] is False

    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
        "de0000000c4944415478da6360000002000155a3f0800000000049454e44ae426082"
    )
    upload = await test_client.put(
        "/api/users/me/avatar", headers=headers, files={"file": ("avatar.png", png_bytes, "image/png")}
    )
    assert upload.status_code == 200
    assert upload.json()["has_avatar"] is True

    me = await test_client.get("/api/auth/me", headers=headers)
    assert me.json()["user"]["has_avatar"] is True

    view = await test_client.get(f"/api/users/{user.id}/avatar", headers=headers)
    assert view.status_code == 200
    assert view.content == png_bytes

    remove = await test_client.delete("/api/users/me/avatar", headers=headers)
    assert remove.status_code == 204

    view_after_remove = await test_client.get(f"/api/users/{user.id}/avatar", headers=headers)
    assert view_after_remove.status_code == 404


async def test_avatar_upload_rejects_wrong_content_type(test_client, db_session):
    user = await make_user(db_session, global_role=Role.ADMIN)
    headers = await auth_headers(user)
    upload = await test_client.put(
        "/api/users/me/avatar", headers=headers, files={"file": ("avatar.gif", b"not-a-real-gif", "image/gif")}
    )
    assert upload.status_code == 400
