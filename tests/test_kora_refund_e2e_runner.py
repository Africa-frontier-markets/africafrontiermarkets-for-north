import os

os.environ.setdefault("SECRET_KEY", "s" * 48)
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

import pytest

from scripts.kora_sandbox_refund_e2e import poll_charge, poll_refund, status_of


class FakeRefundClient:
    def __init__(self):
        self.charge_calls = []
        self.refund_calls = []
        self.charge_statuses = [{"status": "processing"}, {"status": "success"}]
        self.refund_statuses = [{"status": "processing"}, {"status": "settled"}]

    async def verify_charge(self, *, reference):
        self.charge_calls.append(reference)
        return self.charge_statuses[min(len(self.charge_calls) - 1, len(self.charge_statuses) - 1)]

    async def get_refund(self, *, refund_reference):
        self.refund_calls.append(refund_reference)
        return self.refund_statuses[min(len(self.refund_calls) - 1, len(self.refund_statuses) - 1)]


@pytest.mark.asyncio
async def test_poll_charge_stops_on_success_without_extra_calls():
    client = FakeRefundClient()
    result = await poll_charge(client, "payin-1", attempts=5, delay=0)
    assert status_of(result) == "success"
    assert client.charge_calls == ["payin-1", "payin-1"]


@pytest.mark.asyncio
async def test_poll_refund_accepts_settled_and_uses_one_reference():
    client = FakeRefundClient()
    result = await poll_refund(client, "refund-1", attempts=5, delay=0)
    assert status_of(result) == "settled"
    assert client.refund_calls == ["refund-1", "refund-1"]


def test_status_of_supports_nested_api_status_fields():
    assert status_of({"payment_status": "completed"}) == "completed"
    assert status_of({"status": "FAILED"}) == "failed"
    assert status_of({}) == ""


class FlakyRefundClient(FakeRefundClient):
    def __init__(self):
        super().__init__()
        self.failed_once = False

    async def verify_charge(self, *, reference):
        from payment_hub.kora_client import KoraClientError
        if not self.failed_once:
            self.failed_once = True
            raise KoraClientError("temporary upstream error")
        return await super().verify_charge(reference=reference)


@pytest.mark.asyncio
async def test_poll_charge_retries_transient_kora_error():
    client = FlakyRefundClient()
    result = await poll_charge(client, "payin-flaky", attempts=5, delay=0)
    assert status_of(result) == "success"
    assert client.charge_calls == ["payin-flaky", "payin-flaky"]
