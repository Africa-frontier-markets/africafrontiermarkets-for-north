import pytest
from unittest.mock import patch

from payment_hub.psp_router import psp_router, PSPRouter
from payment_hub.models import PSPType
from config.exceptions import CurrencyNotSupportedError, PaymentError


def test_psp_select_by_currency():
    """Selection PSP par devise avec routing regional."""
    # XOF -> Kora (west_africa priority)
    psp = psp_router.select_psp("XOF", region="west_africa")
    assert psp in [PSPType.KORA, PSPType.FINCRA, PSPType.FLUTTERWAVE]

    # ZAR -> Fincra ou Stripe (south_africa)
    psp = psp_router.select_psp("ZAR", region="south_africa")
    assert psp in [PSPType.FINCRA, PSPType.STRIPE]

    # USD -> Stripe ou Fincra (international)
    psp = psp_router.select_psp("USD", region="international")
    assert psp in [PSPType.STRIPE, PSPType.FINCRA]


def test_psp_unsupported_currency():
    """Devise non supportee levee CurrencyNotSupportedError."""
    with pytest.raises(CurrencyNotSupportedError):
        psp_router.select_psp("XYZ")


def test_psp_no_credentials_fallback():
    """Si premier PSP sans credentials, fallback sur suivant."""
    with patch.object(PSPRouter, '_has_credentials', return_value=False):
        with pytest.raises(PaymentError):
            psp_router.select_psp("XOF")


def test_psp_currency_case_insensitive():
    """Devise insensible a la casse."""
    psp1 = psp_router.select_psp("xof")
    psp2 = psp_router.select_psp("XOF")
    assert psp1 == psp2
