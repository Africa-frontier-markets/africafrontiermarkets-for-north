from market_gateway.alpaca_trading import AlpacaTradingClient


def test_trading_api_client_requires_a_dedicated_key_pair():
    client = AlpacaTradingClient(api_key="trading-key", api_secret="trading-secret")

    assert client.configured is True
    assert client.base_url == "https://api.alpaca.markets"
    assert client.market_data_base_url == "https://data.alpaca.markets"
    assert client._headers() == {
        "APCA-API-KEY-ID": "trading-key",
        "APCA-API-SECRET-KEY": "trading-secret",
    }


def test_trading_api_client_rejects_order_writes_during_transition():
    import asyncio

    client = AlpacaTradingClient(api_key="trading-key", api_secret="trading-secret")

    try:
        asyncio.run(client.request("POST", "/v2/orders", json={"symbol": "AAPL"}))
    except RuntimeError as exc:
        assert "read-only" in str(exc)
    else:
        raise AssertionError("write request must be rejected")
