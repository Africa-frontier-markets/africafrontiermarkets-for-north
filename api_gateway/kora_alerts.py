"""Webhook failure notifications for AFM/Kora.

The alert destination is optional and configured server-side. No API keys or
webhook secrets are included in alert payloads.
"""

from __future__ import annotations

import httpx

from config.config import get_settings
from config.logging_config import configure_logging

logger = configure_logging()


async def notify_kora_failure(*, event_id: str, event_type: str, failure_count: int, error: str) -> None:
    """Notify the team after the configured number of recent failures.

    If no destination is configured, the event is still logged so the service
    remains useful on the zero-cost baseline infrastructure.
    """
    settings = get_settings()
    threshold = settings.kora_webhook_alert_threshold
    if failure_count < threshold:
        return

    alert = {
        "source": "afm-kora-webhook",
        "event_id": event_id,
        "event_type": event_type,
        "failure_count": failure_count,
        "message": "Repeated webhook processing failures detected",
        "error": error[:255],
    }
    destination = settings.kora_webhook_alert_url
    if not destination:
        logger.error("Kora webhook alert threshold reached", **alert)
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(destination, json=alert)
            response.raise_for_status()
        logger.warning("Kora webhook alert sent", event_id=event_id, failure_count=failure_count)
    except httpx.HTTPError as exc:
        logger.error("Kora webhook alert delivery failed", event_id=event_id, error=str(exc)[:255])
