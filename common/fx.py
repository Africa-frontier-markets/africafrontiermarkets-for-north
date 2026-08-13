"""
AFM Shared FX Utilities — single source of truth for currency conversion.

🔴 BUG FIXED: `payment_hub/payment_service.py` and
`revenue_engine/revenue_engine.py` each had their own AFM-fee calculation.
`payment_service._calculate_afm_fee` correctly converted the USD min/max fee
bounds into the transaction's local currency before applying them.
`revenue_engine.calculate_fee` did NOT — it applied the raw $0.50 / $50.00
bounds directly to non-USD amounts, which is meaningless (e.g. a $50 cap
applied to an XOF amount is worth about $0.08, not $50). Since
revenue_engine is the module documented as the canonical B2B revenue model,
this made the "official" fee model contradict the one actually charged in
production.

Both modules now import the same FX table and conversion helper from here,
so there is exactly one place that defines "what does $0.50 mean in XOF".
"""

from decimal import Decimal, ROUND_HALF_UP

# Approximate FX rates to USD. Replace with a real FX rate service in
# production — these are illustrative only and will drift.
FX_RATES_TO_USD: dict[str, Decimal] = {
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


def convert_usd_bounds(
    min_usd: Decimal,
    max_usd: Decimal,
    currency: str,
    logger=None,
) -> tuple[Decimal, Decimal]:
    """
    Convert USD min/max fee bounds into the given local currency.

    Returns (min_local, max_local).
    """
    currency = currency.upper()

    if currency == "USD":
        return min_usd, max_usd

    rate = FX_RATES_TO_USD.get(currency)
    if not rate:
        if logger:
            logger.warning(f"No FX rate for {currency}, using USD bounds as-is")
        return min_usd, max_usd

    # local = usd / (local_units_per_usd... actually rate is "1 unit of
    # currency in USD", so local_amount = usd_amount / rate)
    min_local = (min_usd / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    max_local = (max_usd / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    return min_local, max_local
