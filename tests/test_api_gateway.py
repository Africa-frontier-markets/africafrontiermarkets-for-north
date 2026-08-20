from fastapi.testclient import TestClient

from api_gateway.main import app


client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_root_endpoint():
    response = client.get("/")
    assert response.status_code == 200
    assert "Africa Frontier Markets" in response.text


def test_trading_read_routes_are_declared_without_order_routes():
    paths = {route.path for route in app.routes}

    assert "/api/v1/trading/assets" in paths
    assert "/api/v1/trading/instruments" in paths
    assert "/api/v1/trading/instruments/{symbol:path}/history" in paths
    assert "/api/v1/trading/market-snapshots" in paths
    assert "/api/v1/trading/snapshots" in paths
    assert "/api/v1/trading/portfolio" in paths
    assert "/api/v1/trading/portfolio/transactions" in paths
    assert "/api/v1/trading/orders" not in paths


def test_virtual_portfolio_routes_require_a_bearer_token():
    assert client.get("/api/v1/trading/portfolio").status_code == 401
    assert client.get("/api/v1/trading/portfolio/transactions").status_code == 401
