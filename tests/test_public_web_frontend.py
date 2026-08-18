from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_web_frontend_is_standalone_from_mobile_expo():
    page = (ROOT / "public" / "afm-web.html").read_text(encoding="utf-8")

    assert "expo-router" not in page
    assert "react-native-css-interop" not in page
    frontend = (ROOT / "public" / "afm-web.js").read_text(encoding="utf-8")
    assert "/api/v1/public/market-products" in frontend
    assert "data-filter=\"crypto\"" in page
    assert "Onboarding et protections client" in page
    assert 'href="/onboarding"' in page
    assert "prestataires de services de paiement" in page
    assert "investment solicitation" in frontend


def test_gateway_serves_public_markets_and_compliance_routes():
    source = (ROOT / "api_gateway" / "main.py").read_text(encoding="utf-8")

    assert '@app.get("/markets")' in source
    assert '@app.get("/compliance")' in source
    assert '@app.get("/onboarding")' in source
    assert 'RedirectResponse(url="/#compliance"' in source
    assert "standalone public web experience" in source
    assert "PUBLIC_MARKET_SYMBOLS" in source
    assert '@app.get("/api/v1/public/market-products")' in source
    for symbol in ('"AAPL"', '"MSFT"', '"NVDA"', '"BTC/USD"', '"ETH/USD"'):
        assert symbol in source
    assert "get_crypto_bars" in source


def test_crypto_market_data_client_uses_official_crypto_bars_route():
    source = (ROOT / "market_gateway" / "alpaca_broker.py").read_text(encoding="utf-8")

    assert "async def get_crypto_bars" in source
    assert '"/v1beta3/crypto/us/bars"' in source
