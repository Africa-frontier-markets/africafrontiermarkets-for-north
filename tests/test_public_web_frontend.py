from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_web_frontend_is_standalone_from_mobile_expo():
    page = (ROOT / "public" / "afm-web.html").read_text(encoding="utf-8")

    assert "expo-router" not in page
    assert "react-native-css-interop" not in page
    assert "/api/v1/broker/instruments" in (ROOT / "public" / "afm-web.js").read_text(encoding="utf-8")
    assert "Onboarding et protections client" in page


def test_gateway_serves_public_markets_and_compliance_routes():
    source = (ROOT / "api_gateway" / "main.py").read_text(encoding="utf-8")

    assert '@app.get("/markets")' in source
    assert '@app.get("/compliance")' in source
    assert "standalone public web experience" in source
