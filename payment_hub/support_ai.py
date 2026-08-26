"""AFM corrective-support policy engine.

The module is intentionally deterministic by default. It produces a sanitized,
structured diagnosis and a bounded remediation proposal. It never executes SQL,
changes payment state, sends credentials, or calls a payment provider.
An optional LLM advisor can be layered on later, but policy actions remain
allow-listed and require an explicit operational approval.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any, Mapping


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
