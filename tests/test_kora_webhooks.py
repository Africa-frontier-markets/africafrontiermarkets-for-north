import hashlib
import hmac
import json
import os
from types import SimpleNamespace

os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-settings-validation-123456"
os.environ["DATABASE_URL"] = "postgresql+asyncpg://test:test@localhost/test"
os.environ["REDIS_URL"] = "redis://localhost:6379"

import pytest
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from api_gateway.main import kora_webhook
from config.security import verify_kora_webhook_signature


SECRET = "webhook-test-secret"


def make_request(body: dict, signature: str = "") -> Request:
    raw = json.dumps(body, separators=(",", ":")).encode()
    headers = [(b"content-type", b"application/json"), (b"x-korapay-signature", signature.encode())]
    scope = {"type": "http", "method": "POST", "path": "/webhooks/kora", "headers": headers}
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


def sign(data: dict) -> str:
    canonical = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    return hmac.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest()


# Fixture shape copied from Kora's documented Mobile Money webhook example.
KORA_DOCUMENTED_MOBILE_MONEY_FIXTURE = {
    "event": "charge.success",
    "data": {
        "fee": 10,
        "amount": 1000,
        "status": "success",
        "currency": "KES",
        "reference": "merchant-payment-reference-001",
        "payment_method": "mobile_money",
        "payment_reference": "merchant-payment-reference-001",
    },
}


@pytest.mark.asyncio
async def test_kora_documented_mobile_money_fixture_signature_is_deterministic():
    data = KORA_DOCUMENTED_MOBILE_MONEY_FIXTURE["data"]
    signature = sign(data)
    assert verify_kora_webhook_signature(data, signature, SECRET)
    assert signature == "badcb07268a64ead80e590e44e79872c5cc60c12402d9e0595834fe48219f4b3"


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDb:
    def __init__(self, duplicate=False, existing_status="processed"):
        self.duplicate = duplicate
        self.existing = SimpleNamespace(status=existing_status) if duplicate else None
        self.records = []
        self.rolled_back = False
        self.commit_count = 0

    def add(self, value):
        self.records.append(value)

    async def commit(self):
        self.commit_count += 1
        if self.duplicate and self.commit_count == 1:
            raise IntegrityError("insert", {}, Exception("duplicate"))

    async def rollback(self):
        self.rolled_back = True

    async def execute(self, _query):
        return FakeResult(self.existing or (self.records[0] if self.records else None))

    async def scalar(self, _query):
        return 1 if any(getattr(record, "status", None) == "failed" for record in self.records) else 0


@pytest.mark.asyncio
async def test_kora_signature_uses_data_object():
    data = {"reference": "evt-1", "amount": 100, "currency": "XOF"}
    assert verify_kora_webhook_signature(data, sign(data), SECRET)
    assert not verify_kora_webhook_signature(data, "0" * 64, SECRET)


@pytest.mark.asyncio
async def test_kora_webhook_uses_kora_secret_key_fallback(monkeypatch):
    monkeypatch.setattr("api_gateway.main.get_settings", lambda: SimpleNamespace(
        kora_webhook_secret=None,
        kora_secret_key=SECRET,
        kora_webhook_alert_threshold=3,
        kora_webhook_alert_url=None,
    ))
    data = {"reference": "evt-secret-key", "amount": 100, "currency": "XOF"}
    body = {"event": "payment.success", "data": data}
    response = await kora_webhook(make_request(body, sign(data)), FakeDb())
    assert response["status"] == "processed"
    assert response["event_id"].startswith("payment.success:evt-secret-key:")


@pytest.mark.asyncio
async def test_kora_webhook_marks_business_event_processed(monkeypatch):
    monkeypatch.setattr("api_gateway.main.get_settings", lambda: SimpleNamespace(
        kora_webhook_secret=SECRET,
        kora_secret_key=None,
        kora_webhook_alert_threshold=3,
        kora_webhook_alert_url=None,
    ))
    data = {"reference": "evt-1", "amount": 100, "currency": "XOF"}
    body = {"event": "payment.success", "data": data}
    db = FakeDb()
    response = await kora_webhook(make_request(body, sign(data)), db)
    assert response["status"] == "processed"
    assert response["event_id"].startswith("payment.success:evt-1:")
    assert db.records[0].status == "processed"
    assert db.records[0].processed_at is not None
    assert db.records[0].payload_hash == hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.asyncio
async def test_kora_webhook_duplicate_is_acknowledged_without_processing(monkeypatch):
    monkeypatch.setattr("api_gateway.main.get_settings", lambda: SimpleNamespace(
        kora_webhook_secret=SECRET,
        kora_secret_key=None,
        kora_webhook_alert_threshold=3,
        kora_webhook_alert_url=None,
    ))
    data = {"reference": "evt-duplicate", "amount": 100, "currency": "XOF"}
    body = {"event": "payment.success", "data": data}
    response = await kora_webhook(make_request(body, sign(data)), FakeDb(duplicate=True))
    assert response["status"] == "already_processed"
    assert response["event_id"].startswith("payment.success:evt-duplicate:")


@pytest.mark.asyncio
async def test_kora_webhook_records_failure_and_alerts_after_threshold(monkeypatch):
    monkeypatch.setattr("api_gateway.main.get_settings", lambda: SimpleNamespace(
        kora_webhook_secret=SECRET,
        kora_secret_key=None,
        kora_webhook_alert_threshold=1,
        kora_webhook_alert_url=None,
    ))
    alerts = []

    async def capture_alert(**kwargs):
        alerts.append(kwargs)

    monkeypatch.setattr("api_gateway.main.notify_kora_failure", capture_alert)
    body = {"event": "payment.success", "data": {"amount": 100, "currency": "XOF"}}
    with pytest.raises(Exception) as exc_info:
        await kora_webhook(make_request(body, sign(body["data"])), FakeDb())
    assert getattr(exc_info.value, "status_code", None) == 500
    assert alerts and alerts[0]["failure_count"] == 1


@pytest.mark.asyncio
async def test_kora_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr("api_gateway.main.get_settings", lambda: SimpleNamespace(
        kora_webhook_secret=SECRET,
        kora_secret_key=None,
        kora_webhook_alert_threshold=3,
        kora_webhook_alert_url=None,
    ))
    data = {"reference": "evt-invalid", "amount": 100, "currency": "XOF"}
    body = {"event": "payment.success", "data": data}
    response = await kora_webhook(make_request(body, "invalid"), FakeDb())
    assert response == {"status": "ignored", "reason": "invalid_signature"}


@pytest.mark.asyncio
async def test_kora_webhook_fallback_event_id_includes_event_and_payload_hash(monkeypatch):
    monkeypatch.setattr("api_gateway.main.get_settings", lambda: SimpleNamespace(
        kora_webhook_secret=SECRET,
        kora_secret_key=None,
        kora_webhook_alert_threshold=3,
        kora_webhook_alert_url=None,
    ))
    data = {"reference": "same-ref", "amount": 100, "currency": "XOF"}
    body = {"event": "charge.success", "data": data}
    db = FakeDb()
    response = await kora_webhook(make_request(body, sign(data)), db)
    assert response["event_id"].startswith("charge.success:same-ref:")
    assert len(response["event_id"]) <= 128
