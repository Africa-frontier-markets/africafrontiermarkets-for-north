import json

import pytest

from payment_hub.support_ai import analyze_incident, enrich_with_external_llm


@pytest.mark.asyncio
async def test_llm_enrichment_is_disabled_without_configuration(monkeypatch):
    monkeypatch.delenv("SUPPORT_AI_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("SUPPORT_AI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SUPPORT_AI_LLM_MODEL", raising=False)

    decision = await enrich_with_external_llm(
        analyze_incident("Kora timeout", {"api_key": "hidden"})
    )

    assert decision.llm_note is None
    assert decision.sanitized_context["api_key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_llm_receives_only_redacted_context_and_cannot_change_action(monkeypatch):
    monkeypatch.setenv("SUPPORT_AI_LLM_BASE_URL", "https://llm.test/v1")
    monkeypatch.setenv("SUPPORT_AI_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SUPPORT_AI_LLM_MODEL", "test-model")
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": json.dumps({"note": "Retry status lookup", "confidence": 0.9})}}]}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers, json):
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("payment_hub.support_ai.httpx.AsyncClient", FakeClient)
    decision = analyze_incident("Kora HTTP 503 timeout", {"pin": "secret", "event_id": "evt-1"})
    enriched = await enrich_with_external_llm(decision)

    assert enriched.proposed_action == "retry_status_lookup"
    assert enriched.llm_note == "Retry status lookup"
    assert captured["body"]["model"] == "test-model"
    assert "hidden" not in json.dumps(captured["body"])
    assert "[REDACTED]" in json.dumps(captured["body"])
    assert captured["timeout"] == 8.0
