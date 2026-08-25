import hashlib
import hmac
import json
import os

os.environ.setdefault("SECRET_KEY", "s" * 48)
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from config.security import verify_kora_webhook_signature

SECRET = "corridor-matrix-test-secret"
CORRIDORS = {
    "CI-GH": ("XOF", "GHS"),
    "CI-NG": ("XOF", "NGN"),
    "BJ-NG": ("XOF", "NGN"),
    "CM-NG": ("XAF", "NGN"),
    "CM-CI": ("XAF", "XOF"),
    "CI-CM": ("XOF", "XAF"),
}


def signature(data: dict) -> str:
    canonical = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    return hmac.new(SECRET.encode(), canonical, hashlib.sha256).hexdigest()


def fallback_event_id(event: str, reference: str, body: dict) -> str:
    payload_hash = hashlib.sha256(json.dumps(body, separators=(",", ":")).encode()).hexdigest()
    return f"{event}:{reference}:{payload_hash}"[:128]


def test_hmac_and_event_id_are_valid_for_every_configured_corridor():
    ids = set()
    for corridor, (source, destination) in CORRIDORS.items():
        data = {
            "amount": 100000,
            "currency": source,
            "reference": f"matrix-{corridor}",
            "status": "success",
            "metadata": {"corridor": corridor, "destination_currency": destination},
        }
        body = {"event": "charge.success", "data": data}
        assert verify_kora_webhook_signature(data, signature(data), SECRET)
        ids.add(fallback_event_id("charge.success", data["reference"], body))
    assert len(ids) == len(CORRIDORS)


def test_same_reference_different_event_or_payload_never_collides():
    body_a = {"event": "charge.success", "data": {"reference": "same", "status": "success", "currency": "XAF"}}
    body_b = {"event": "refund.success", "data": {"reference": "same", "status": "success", "currency": "XAF"}}
    assert fallback_event_id(body_a["event"], "same", body_a) != fallback_event_id(body_b["event"], "same", body_b)
