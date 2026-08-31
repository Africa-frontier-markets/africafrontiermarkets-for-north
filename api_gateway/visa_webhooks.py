"""Secure Visa Direct webhook helpers for AFM.

This module is intentionally self-contained so it can be copied into the
production FastAPI source once the deployed repository is identified.

Transport TLS/mTLS must be terminated by a trusted ingress or by Uvicorn.
The application never trusts arbitrary client-certificate headers from the
public internet; a proxy header is accepted only when an explicit proxy
shared secret is configured and matches.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Any, Mapping

from fastapi import APIRouter, Header, HTTPException, Request, status

MAX_VISA_BODY_BYTES = 1_048_576


@dataclass(frozen=True)
class VisaWebhookSettings:
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    webhook_secret: str | None = field(default_factory=lambda: os.getenv("VISA_WEBHOOK_SHARED_SECRET") or None)
    proxy_secret: str | None = field(default_factory=lambda: os.getenv("VISA_MTLS_PROXY_SECRET") or None)
    require_mtls: bool = field(default_factory=lambda: os.getenv("VISA_MTLS_REQUIRED", "false").lower() == "true")

    @property
    def production(self) -> bool:
        return self.environment == "production"


def _constant_time_signature_match(body: bytes, supplied: str, secret: str) -> bool:
    supplied_value = supplied.removeprefix("sha256=").strip().lower()
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(supplied_value, expected)


def _verify_mtls_proxy(headers: Mapping[str, str], settings: VisaWebhookSettings) -> bool:
    """Validate an ingress assertion only when it is authenticated.

    A plain X-Client-Cert-Verify header is not trusted. In production the
    ingress must strip that header from external requests and add a signed
    assertion in X-AFM-MTLS-Assertion using VISA_MTLS_PROXY_SECRET.
    """
    if not settings.require_mtls:
        return True
    assertion = headers.get("x-afm-mtls-assertion", "")
    verified = headers.get("x-client-cert-verify", "").upper()
    if not settings.proxy_secret or verified != "SUCCESS":
        return False
    expected = hmac.new(
        settings.proxy_secret.encode(),
        b"visa-client-cert:SUCCESS",
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(assertion, expected)


def validate_visa_request(
    body: bytes,
    headers: Mapping[str, str],
    settings: VisaWebhookSettings,
) -> dict[str, Any]:
    if len(body) > MAX_VISA_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Visa webhook body too large")
    if not _verify_mtls_proxy(headers, settings):
        raise HTTPException(status_code=401, detail="Valid Visa mTLS assertion required")

    if settings.webhook_secret:
        supplied = headers.get("x-visa-signature", "")
        if not supplied or not _constant_time_signature_match(body, supplied, settings.webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid Visa webhook signature")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Visa payload must be an object")
    return payload


def build_visa_router(settings: VisaWebhookSettings | None = None) -> APIRouter:
    cfg = settings or VisaWebhookSettings()
    router = APIRouter(prefix="/webhooks/visa", tags=["visa-webhooks"])

    async def receive(request: Request, x_request_id: str | None = Header(default=None)):
        body = await request.body()
        payload = validate_visa_request(body, request.headers, cfg)
        # Replace this log-only section with the durable idempotent ledger
        # handler. Do not initiate a payment from a callback without a stored
        # event ID and an idempotency constraint.
        event_id = payload.get("eventId") or payload.get("event_id") or x_request_id
        return {"status": "received", "event_id": event_id}

    router.add_api_route("/receive-side", receive, methods=["POST"], status_code=status.HTTP_200_OK)
    router.add_api_route("/issuer", receive, methods=["POST"], status_code=status.HTTP_200_OK)
    return router


# Example for api_gateway.main:
# app.include_router(build_visa_router())
