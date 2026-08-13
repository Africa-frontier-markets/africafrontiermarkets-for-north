"""Dashboard service with centralized FX_TO_USD. V45 Correction."""

from decimal import Decimal, ROUND_HALF_UP


class DashboardService:
    """
    V45 Correction: Centralized FX rates. Single source of truth.
    """

    FX_TO_USD = {
        "XOF": Decimal("0.00165"),
        "XAF": Decimal("0.00165"),
        "NGN": Decimal("0.00064"),
        "KES": Decimal("0.0077"),
        "GHS": Decimal("0.066"),
        "ZAR": Decimal("0.055"),
        "USD": Decimal("1.0"),
        "EUR": Decimal("1.08"),
        "GBP": Decimal("1.27"),
    }

    def convert_to_usd(self, amount: Decimal, currency: str) -> Decimal:
        currency = currency.upper()
        if currency not in self.FX_TO_USD:
            raise ValueError(f"Currency {currency} not in FX mapping")

        rate = self.FX_TO_USD[currency]
        return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def convert_from_usd(self, amount_usd: Decimal, target_currency: str) -> Decimal:
        target_currency = target_currency.upper()
        if target_currency not in self.FX_TO_USD:
            raise ValueError(f"Currency {target_currency} not in FX mapping")

        rate = self.FX_TO_USD[target_currency]
        if rate == 0:
            raise ValueError(f"Invalid rate for {target_currency}")

        return (amount_usd / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def get_portfolio_value(self, holdings: list[dict]) -> dict:
        total_usd = Decimal("0")
        by_currency = {}

        for holding in holdings:
            currency = holding["currency"]
            amount = Decimal(str(holding["amount"]))
            usd_value = self.convert_to_usd(amount, currency)

            by_currency[currency] = {
                "local_amount": amount,
                "usd_value": usd_value,
            }
            total_usd += usd_value

        return {
            "total_usd": total_usd.quantize(Decimal("0.01")),
            "by_currency": by_currency,
            "fx_rates_used": {k: str(v) for k, v in self.FX_TO_USD.items()},
        }


dashboard_service = DashboardService()
