from api_gateway.main import build_activity_page, normalize_broker_activity, paginate_instruments


def test_normalize_trade_activity_uses_real_fill_fields():
    transaction = normalize_broker_activity(
        {
            "id": "fill-1",
            "activity_type": "FILL",
            "symbol": "AAPL",
            "side": "buy",
            "qty": "2",
            "price": "10.50",
            "transaction_time": "2026-08-17T12:00:00Z",
        }
    )

    assert transaction == {
        "id": "fill-1",
        "activity_type": "FILL",
        "symbol": "AAPL",
        "side": "buy",
        "quantity": "2",
        "amount": "-21.00",
        "occurred_at": "2026-08-17T12:00:00Z",
        "description": "Buy AAPL",
    }


def test_normalize_non_trade_activity_keeps_broker_net_amount():
    transaction = normalize_broker_activity(
        {
            "id": "dividend-1",
            "activity_type": "DIV",
            "symbol": "T",
            "net_amount": "1.02",
            "date": "2026-08-17",
        }
    )

    assert transaction["amount"] == "1.02"
    assert transaction["occurred_at"] == "2026-08-17"
    assert transaction["description"] == "DIV · T"


def test_activity_page_exposes_last_real_activity_as_next_cursor_only_when_full():
    activities = [
        {"id": "activity-1", "activity_type": "DIV", "net_amount": "1"},
        {"id": "activity-2", "activity_type": "DIV", "net_amount": "2"},
    ]
    full_page = build_activity_page("account-1", activities, limit=2)
    partial_page = build_activity_page("account-1", activities, limit=3)

    assert full_page["next_page_token"] == "activity-2"
    assert partial_page["next_page_token"] is None


def test_paginate_instruments_filters_real_metadata_before_slicing():
    assets = [
        {"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"},
        {"symbol": "MSFT", "name": "Microsoft Corporation", "exchange": "NASDAQ"},
        {"symbol": "AFRM", "name": "Affirm Holdings", "exchange": "NASDAQ"},
    ]
    page, total = paginate_instruments(assets, query="af", page=1, page_size=1)

    assert total == 1
    assert page == [{"symbol": "AFRM", "name": "Affirm Holdings", "exchange": "NASDAQ"}]
