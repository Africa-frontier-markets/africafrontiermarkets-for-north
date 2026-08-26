import asyncio
from types import SimpleNamespace
from uuid import uuid4

from payment_hub.reconciliation import enqueue_reconciliation, next_backoff, normalize_kora_status
from payment_hub.models import PaymentStatus


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class FakeDb:
    def __init__(self):
        self.task = None
        self.added = []

    async def execute(self, query):
        return Result(self.task)

    def add(self, value):
        self.added.append(value)
        self.task = value


def test_kora_statuses_normalize_to_safe_states():
    assert normalize_kora_status("processing") == "processing"
    assert normalize_kora_status("requires_authorization") == "processing"
    assert normalize_kora_status("settled") == "completed"
    assert normalize_kora_status("failed") == "failed"
    assert normalize_kora_status("unknown_provider_state") == "processing"


def test_reconciliation_backoff_is_bounded():
    assert [next_backoff(i) for i in range(1, 7)] == [15, 30, 60, 120, 240, 300]


def test_processing_payin_enqueues_one_reconciliation_task():
    transaction = SimpleNamespace(
        id=uuid4(),
        psp_transaction_id="KPY-CA-processing",
        txn_metadata={},
        status=PaymentStatus.PROCESSING,
    )
    db = FakeDb()

    async def scenario():
        first = await enqueue_reconciliation(
            db,
            transaction,
            {
                "status": "processing",
                "transaction_reference": "tx-processing",
                "payment_reference": "pay-processing",
            },
        )
        second = await enqueue_reconciliation(
            db,
            transaction,
            {
                "status": "processing",
                "payment_reference": "pay-processing",
            },
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first is second
    assert len(db.added) == 1
    assert first.payment_reference == "pay-processing"
    assert first.transaction_reference == "tx-processing"
    assert first.state == "scheduled"
    assert transaction.txn_metadata["reconciliation_status"] == "scheduled"
