from api_gateway.main import normalize_broker_activity


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

