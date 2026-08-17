from api_gateway.auth import is_valid_mobile_exchange_secret


def test_mobile_exchange_secret_requires_exact_match():
    configured = "x" * 48

    assert is_valid_mobile_exchange_secret(configured, configured)
    assert not is_valid_mobile_exchange_secret("y" * 48, configured)
    assert not is_valid_mobile_exchange_secret(None, configured)
    assert not is_valid_mobile_exchange_secret(configured, None)
