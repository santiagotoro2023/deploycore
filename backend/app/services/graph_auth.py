import asyncio

import msal

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]


def _acquire_token_sync(tenant_id: str, client_id: str, client_secret: str) -> str:
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "failed to acquire a Graph API token"))
    return result["access_token"]


async def acquire_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """msal's ConfidentialClientApplication is a sync client (no asyncio
    variant), run in a worker thread same as the pyvmomi/WinRM calls
    elsewhere in this app. Shared by services/m365.py (Mail.Send) and
    services/teams.py (TeamsActivity.Send) - same app-only client-credential
    flow, only the eventual Graph call and permissions differ."""
    return await asyncio.to_thread(_acquire_token_sync, tenant_id, client_id, client_secret)
