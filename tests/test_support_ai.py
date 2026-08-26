from payment_hub.support_ai import analyze_incident


def test_processing_incident_proposes_reconciliation_only():
    decision = analyze_incident(
        "Kora status processing",
        {"payment_reference": "KPY-TEST-1", "pin": "never-log"},
    )

    assert decision.category == "payment_processing"
    assert decision.proposed_action == "enqueue_reconciliation"
    assert decision.auto_action_allowed is True
    assert decision.requires_human_approval is False
    assert decision.sanitized_context["pin"] == "[REDACTED]"


def test_database_incident_requires_human_approval():
    decision = analyze_incident(
        "Undefined column psp_payment_reference",
        {"component": "alembic", "api_key": "never-log"},
    )

    assert decision.category == "database_schema"
    assert decision.proposed_action == "open_incident"
    assert decision.auto_action_allowed is False
    assert decision.requires_human_approval is True
    assert decision.sanitized_context["api_key"] == "[REDACTED]"


def test_transient_provider_error_is_retryable_without_money_movement():
    decision = analyze_incident(
        "Kora returned HTTP 503 timeout",
        {"event_type": "pay-in"},
    )

    assert decision.category == "provider_transient"
    assert decision.proposed_action == "retry_status_lookup"
    assert decision.auto_action_allowed is True
    assert decision.requires_human_approval is False
