"""Small, redacted Meta WhatsApp Cloud API client for authentication messages."""

from __future__ import annotations

import asyncio
import json
import re
from urllib import error, request

from config.config import get_settings


class WhatsAppApiError(RuntimeError):
    pass


def normalize_whatsapp_number(value: str) -> str:
    number = "".join(ch for ch in value if ch.isdigit())
    if not re.fullmatch(r"[1-9][0-9]{7,14}", number):
        raise WhatsAppApiError("WhatsApp number must use international format")
    return number


def _send_template_sync(recipient: str, code: str) -> None:
    settings = get_settings()
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise WhatsAppApiError("WhatsApp Business is not configured")
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "template",
        "template": {
            "name": settings.whatsapp_otp_template_name,
            "language": {"code": settings.whatsapp_otp_template_language},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": code}],
                },
            ],
        },
    }
    endpoint = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages"
    req = request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.whatsapp_access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "afm-whatsapp-auth/1.0",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            body = json.load(response)
    except (error.HTTPError, error.URLError, TimeoutError) as exc:
        raise WhatsAppApiError("WhatsApp authentication message failed") from exc
    if not body.get("messages"):
        raise WhatsAppApiError("WhatsApp returned no message receipt")


async def send_authentication_code(phone: str, code: str) -> None:
    recipient = normalize_whatsapp_number(phone)
    await asyncio.to_thread(_send_template_sync, recipient, code)
