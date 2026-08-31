import hashlib
import hmac

import pytest
from fastapi import HTTPException

from api_gateway.visa_webhooks import VisaWebhookSettings, validate_visa_request


PAYLOAD = b'{"event":"ping","eventId":"evt-123"}'


def test_visa_validation_accepts_sandbox_payload_without_optional_secrets():
    settings = VisaWebhookSettings(webhook_secret=None, proxy_secret=None, require_mtls=False)
    assert validate_visa_request(PAYLOAD, {}, settings)["eventId"] == "evt-123"


def test_visa_validation_requires_valid_hmac_when_configured():
    secret = "visa-test-secret"
    signature = hmac.new(secret.encode(), PAYLOAD, hashlib.sha256).hexdigest()
    settings = VisaWebhookSettings(webhook_secret=secret, proxy_secret=None, require_mtls=False)
    assert validate_visa_request(PAYLOAD, {"x-visa-signature": f"sha256={signature}"}, settings)["event"] == "ping"

    with pytest.raises(HTTPException) as exc:
        validate_visa_request(PAYLOAD, {"x-visa-signature": "sha256=invalid"}, settings)
    assert exc.value.status_code == 401


def test_visa_validation_requires_authenticated_mtls_assertion_when_configured():
    proxy_secret = "proxy-secret"
    assertion = hmac.new(proxy_secret.encode(), b"visa-client-cert:SUCCESS", hashlib.sha256).hexdigest()
    settings = VisaWebhookSettings(webhook_secret=None, proxy_secret=proxy_secret, require_mtls=True)
    headers = {
        "x-client-cert-verify": "SUCCESS",
        "x-afm-mtls-assertion": assertion,
    }
    assert validate_visa_request(PAYLOAD, headers, settings)["eventId"] == "evt-123"

    with pytest.raises(HTTPException) as exc:
        validate_visa_request(PAYLOAD, {"x-client-cert-verify": "SUCCESS"}, settings)
    assert exc.value.status_code == 401
