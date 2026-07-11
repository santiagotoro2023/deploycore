import httpx

from app.services.graph_auth import acquire_token

GRAPH_TIMEOUT_SECONDS = 20
# Teams truncates previewText well before this in the UI, but the API
# itself rejects anything longer - trimming here gives a predictable
# result instead of a 400 from Graph on a long custom message.
PREVIEW_TEXT_MAX_CHARS = 150


async def send_activity_notification(
    *, tenant_id: str, client_id: str, client_secret: str, teams_app_id: str, to_upn: str, message: str
) -> None:
    """Notifies one specific Teams user via Microsoft Graph's Activity
    Feed API - a notification banner plus an entry in that user's
    Activity tab, not a raw chat bubble. This is the documented,
    app-only-auth-compatible way to message a specific person without
    hosting a full Bot Framework bot (a real 1:1 chat via `POST /chats`
    needs a second real user identity as the chat's other member, an app
    registration alone can't be one).

    Two real prerequisites beyond the Entra app registration itself, both
    on the M365 tenant's own side, and both required or this raises with
    Graph's own error text rather than silently doing nothing:

    1. teams_app_id must be a Teams app already published to the org's
       app catalog (Teams admin center -> Manage apps -> Upload), whose
       manifest declares a custom activity type matching activityType
       below, e.g.:
           "activities": {"activityTypes": [
               {"type": "deploymentNotification",
                "description": "DeployCore notification",
                "templateText": "{message}"}
           ]}
    2. The Entra app registration needs TeamsActivity.Send and
       TeamsAppInstallation.ReadWriteForUser.All Application permissions,
       admin-consented.
    """
    token = await acquire_token(tenant_id, client_id, client_secret)
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=GRAPH_TIMEOUT_SECONDS) as client:
        # Best-effort: installs the app for the user if it isn't already
        # (a fresh user who's never had it pushed to them). 409 means
        # already installed, not a real failure; anything else here isn't
        # fatal on its own either, an admin may have installed it
        # tenant-wide already - the actual send below is what determines
        # whether this call succeeded.
        await client.post(
            f"https://graph.microsoft.com/v1.0/users/{to_upn}/teamwork/installedApps",
            json={"teamsApp@odata.bind": f"https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/{teams_app_id}"},
            headers=headers,
        )

        response = await client.post(
            f"https://graph.microsoft.com/v1.0/users/{to_upn}/teamwork/sendActivityNotification",
            json={
                "topic": {"source": "text", "value": "DeployCore"},
                "activityType": "deploymentNotification",
                "previewText": {"content": message[:PREVIEW_TEXT_MAX_CHARS]},
                "templateParameters": [{"name": "message", "value": message}],
            },
            headers=headers,
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Graph sendActivityNotification failed ({response.status_code}): {response.text}")
