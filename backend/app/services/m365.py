import asyncio

import httpx
import msal

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
GRAPH_SEND_MAIL_TIMEOUT_SECONDS = 20


def _acquire_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """msal's ConfidentialClientApplication is a sync client (no asyncio
    variant), run in a worker thread same as the pyvmomi/WinRM calls
    elsewhere in this app."""
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(result.get("error_description", "failed to acquire a Graph API token"))
    return result["access_token"]


async def send_mail(
    *, tenant_id: str, client_id: str, client_secret: str, sender_upn: str, to_email: str, subject: str, body: str
) -> None:
    token = await asyncio.to_thread(_acquire_token, tenant_id, client_id, client_secret)
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to_email}}],
        }
    }
    async with httpx.AsyncClient(timeout=GRAPH_SEND_MAIL_TIMEOUT_SECONDS) as client:
        response = await client.post(
            f"https://graph.microsoft.com/v1.0/users/{sender_upn}/sendMail",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"Graph sendMail failed ({response.status_code}): {response.text}")
