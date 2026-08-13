from decimal import Decimal

from payment_hub.payment_service import payment_service


def test_sha256_lock_id_deterministic():
    """V45: SHA256 stable lock ID is deterministic."""
    lock1 = payment_service._generate_lock_id("user_123", "100.00", "XOF", "2024-01-01")
    lock2 = payment_service._generate_lock_id("user_123", "100.00", "XOF", "2024-01-01")
    assert lock1 == lock2
    assert len(lock1) == 64

    lock3 = payment_service._generate_lock_id("user_123", "100.00", "XOF", "2024-01-02")
    assert lock1 != lock3
