import httpx

from app.services.graph_auth import acquire_token

GRAPH_SEND_MAIL_TIMEOUT_SECONDS = 20


async def send_mail(
    *, tenant_id: str, client_id: str, client_secret: str, sender_upn: str, to_email: str, subject: str, body: str
) -> None:
    token = await acquire_token(tenant_id, client_id, client_secret)
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
