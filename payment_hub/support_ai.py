"""AFM corrective-support policy engine.

The module is intentionally deterministic by default. It produces a sanitized,
structured diagnosis and a bounded remediation proposal. It never executes SQL,
changes payment state, sends credentials, or calls a payment provider.
An optional LLM advisor can be layered on later, but policy actions remain
allow-listed and require an explicit operational approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

import httpx


_SENSITIVE_KEY_RE = re.compile(r"(secret|token|password|pin|otp|authorization|cookie|api[_-]?key)", re.I)


@dataclass(frozen=True)
class SupportDecision:
    incident_key: str
    category: str
    severity: str
    diagnosis: str
    proposed_action: str
    auto_action_allowed: bool
    requires_human_approval: bool
    sanitized_context: dict[str, Any]
    llm_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY_RE.search(str(key)) else _sanitize(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str) and len(value) > 512:
        return value[:512] + "…"
    return value


def _incident_key(error: str, context: Mapping[str, Any]) -> str:
    reference = str(
        context.get("payment_reference")
        or context.get("transaction_reference")
        or context.get("event_id")
        or "unknown"
    )
    digest = hashlib.sha256(f"{error[:160]}:{reference}".encode()).hexdigest()[:16]
    return f"afm-support-{digest}"


def analyze_incident(error: str, context: Mapping[str, Any] | None = None) -> SupportDecision:
    """Return a safe diagnosis and allow-listed proposal for one incident."""
    context = dict(context or {})
    sanitized = _sanitize(context)
    text = f"{error} {context.get('provider_status', '')} {context.get('event_type', '')}".lower()

    if any(marker in text for marker in ("processing", "pending", "requires_authorization", "stk_prompt")):
        return SupportDecision(
            incident_key=_incident_key(error, context),
            category="payment_processing",
            severity="warning",
            diagnosis="Provider payment remains intermediate; preserve funds and reconcile asynchronously.",
            proposed_action="enqueue_reconciliation",
            auto_action_allowed=True,
            requires_human_approval=False,
            sanitized_context=sanitized,
        )

    if any(marker in text for marker in ("timeout", "temporarily", "rate limit", "429", "502", "503", "504")):
        return SupportDecision(
            incident_key=_incident_key(error, context),
            category="provider_transient",
            severity="warning",
            diagnosis="Provider communication is transient; retry with bounded backoff and preserve the current ledger state.",
            proposed_action="retry_status_lookup",
            auto_action_allowed=True,
            requires_human_approval=False,
            sanitized_context=sanitized,
        )

    if any(marker in text for marker in ("does not exist", "undefined column", "schema", "database", "sqlalchemy", "alembic")):
        return SupportDecision(
            incident_key=_incident_key(error, context),
            category="database_schema",
            severity="critical",
            diagnosis="Database or schema mismatch detected; stop automated money movement and require an operator-led migration review.",
            proposed_action="open_incident",
            auto_action_allowed=False,
            requires_human_approval=True,
            sanitized_context=sanitized,
        )

    if any(marker in text for marker in ("signature", "hmac", "invalid webhook")):
        return SupportDecision(
            incident_key=_incident_key(error, context),
            category="webhook_security",
            severity="high",
            diagnosis="Webhook authenticity or canonicalization failed; reject business processing and alert an operator.",
            proposed_action="alert_admin",
            auto_action_allowed=False,
            requires_human_approval=True,
            sanitized_context=sanitized,
        )

    return SupportDecision(
        incident_key=_incident_key(error, context),
        category="unknown",
        severity="high",
        diagnosis="Unclassified incident; preserve financial state and request human review.",
        proposed_action="alert_admin",
        auto_action_allowed=False,
        requires_human_approval=True,
        sanitized_context=sanitized,
    )


async def enrich_with_external_llm(decision: SupportDecision) -> SupportDecision:
    """Optionally enrich a diagnosis; never accepts an LLM action or secret."""
    base_url = os.getenv("SUPPORT_AI_LLM_BASE_URL", "").rstrip("/")
    api_key = os.getenv("SUPPORT_AI_LLM_API_KEY", "")
    model = os.getenv("SUPPORT_AI_LLM_MODEL", "")
    if not base_url or not api_key or not model:
        return decision

    prompt = {
        "incident": decision.category,
        "severity": decision.severity,
        "deterministic_diagnosis": decision.diagnosis,
        "context": decision.sanitized_context,
        "instruction": "Return JSON with only a concise note and confidence 0..1. Never propose an action, secret, SQL, PIN, OTP, payout, refund, or code mutation.",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an AFM support analyst. You may enrich a diagnosis only; deterministic policy remains authoritative."},
            {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
        ],
        "temperature": 0,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "support_note",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "note": {"type": "string", "maxLength": 600},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["note", "confidence"],
                    "additionalProperties": False,
                },
            },
        },
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(f"{base_url}/chat/completions", headers=headers, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            note = json.loads(content).get("note", "").strip()
    except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return decision
    if not note:
        return decision
    return replace(decision, llm_note=note[:600])
