"""Revenue distribution: 35% Ads, 50% Creator, 15% Platform."""
"""
AFM Revenue Engine — B2B Transaction Fee Model

NOT COSY. This is pure fintech:
- Commission per transaction: 25 bps (0.25%)
- Min fee: $0.50
- Max fee: $50.00
- No ads, no creator split, no platform split.
"""

from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass

from config.config import get_settings
from config.telemetry import revenue_split


@dataclass
class RevenueSplit:
    ads: Decimal
    creator: Decimal
    platform: Decimal
    total: Decimal


class RevenueEngine:
    def __init__(self):
        settings = get_settings()
        self.ads_rate = Decimal(str(settings.ads_share))
        self.creator_rate = Decimal(str(settings.creator_share))
        self.platform_rate = Decimal(str(settings.platform_share))

    def calculate_split(self, gross_amount: Decimal) -> RevenueSplit:
        quantize = Decimal("0.01")

        ads = (gross_amount * self.ads_rate).quantize(quantize, rounding=ROUND_HALF_UP)
        creator = (gross_amount * self.creator_rate).quantize(quantize, rounding=ROUND_HALF_UP)
        platform = (gross_amount * self.platform_rate).quantize(quantize, rounding=ROUND_HALF_UP)

        total_split = ads + creator + platform
        if total_split != gross_amount:
            diff = gross_amount - total_split
            if creator >= ads and creator >= platform:
                creator += diff
            elif ads >= platform:
                ads += diff
            else:
                platform += diff

        revenue_split.labels(recipient_type="ads").inc(float(ads))
        revenue_split.labels(recipient_type="creator").inc(float(creator))
        revenue_split.labels(recipient_type="platform").inc(float(platform))

        return RevenueSplit(
            ads=ads.quantize(quantize),
            creator=creator.quantize(quantize),
            platform=platform.quantize(quantize),
            total=gross_amount,
        )


revenue_engine = RevenueEngine()
from config.logging_config import configure_logging
from common.fx import convert_usd_bounds

logger = configure_logging()


@dataclass
class FeeBreakdown:
    gross_amount: Decimal
    afm_fee: Decimal
    afm_fee_bps: int
    net_amount: Decimal
    psp_fee: Decimal  # Estimated PSP fee
    psp_fee_bps: int
    total_cost_to_merchant: Decimal


class AFMRevenueEngine:
    """
    B2B Revenue Engine for AFM.

    AFM charges a commission on every transaction processed.
    This is NOT a creator economy split — it's a pure payment processing fee.
    """

    # AFM commission
    AFM_COMMISSION_BPS = 25  # 0.25%
    AFM_MIN_FEE_USD = Decimal("0.50")
    AFM_MAX_FEE_USD = Decimal("50.00")

    # Estimated PSP fees (for margin calculation)
    PSP_FEE_ESTIMATES = {
        "kora": 15,        # 0.15%
        "fincra": 20,      # 0.20%
        "flutterwave": 14, # 0.14% + 1.4% for cards
        "stripe": 29,      # 2.9% + $0.30
        "mtn_momo": 10,    # 0.10%
        "orange_money": 10, # 0.10%
    }

    def __init__(self):
        settings = get_settings()
        self.commission_bps = int(settings.afm_commission_bps)
        self.min_fee = settings.afm_min_fee_usd
        self.max_fee = settings.afm_max_fee_usd

    def calculate_fee(self, amount: Decimal, currency: str = "USD", psp: str = "kora") -> FeeBreakdown:
        """
        Calculate AFM fee for a transaction.

        Args:
            amount: Transaction amount
            currency: Transaction currency
            psp: PSP used for the transaction

        Returns:
            FeeBreakdown with all fee components
        """
        quantize = Decimal("0.01")

        # AFM commission
        afm_fee = (amount * Decimal(self.commission_bps) / Decimal("10000"))

        # 🔴 FIX: min/max bounds are defined in USD but the transaction may
        # be in XOF/NGN/etc. Applying the raw $0.50/$50.00 numbers to a
        # non-USD amount is meaningless (e.g. a "$50 cap" applied literally
        # to XOF is worth about eight cents). Convert the bounds into the
        # transaction currency first, exactly like payment_service does —
        # both now share the same helper so they can't drift apart again.
        min_fee, max_fee = convert_usd_bounds(self.min_fee, self.max_fee, currency, logger=logger)
        afm_fee = max(afm_fee, min_fee)
        afm_fee = min(afm_fee, max_fee)
        afm_fee = afm_fee.quantize(quantize, rounding=ROUND_HALF_UP)

        # Net amount after AFM fee
        net_amount = amount - afm_fee

        # Estimated PSP fee
        psp_fee_bps = self.PSP_FEE_ESTIMATES.get(psp.lower(), 20)
        psp_fee = (amount * Decimal(psp_fee_bps) / Decimal("10000")).quantize(quantize)

        # Total cost to merchant
        total_cost = afm_fee + psp_fee

        logger.info(
            "Fee calculated",
            amount=str(amount),
            currency=currency,
            afm_fee=str(afm_fee),
            psp_fee=str(psp_fee),
            net=str(net_amount),
        )

        return FeeBreakdown(
            gross_amount=amount.quantize(quantize),
            afm_fee=afm_fee,
            afm_fee_bps=self.commission_bps,
            net_amount=net_amount.quantize(quantize),
            psp_fee=psp_fee,
            psp_fee_bps=psp_fee_bps,
            total_cost_to_merchant=total_cost.quantize(quantize),
        )

    def calculate_monthly_revenue(self, transaction_volume: Decimal, avg_transaction_size: Decimal) -> dict:
        """
        Estimate monthly revenue based on volume.

        Args:
            transaction_volume: Number of transactions per month
            avg_transaction_size: Average transaction amount in USD
        """
        total_volume = transaction_volume * avg_transaction_size

        # AFM revenue
        afm_revenue = (total_volume * Decimal(self.commission_bps) / Decimal("10000"))

        # Cap at max fee per transaction
        max_revenue = transaction_volume * self.max_fee
        afm_revenue = min(afm_revenue, max_revenue)

        # Floor at min fee per transaction
        min_revenue = transaction_volume * self.min_fee
        afm_revenue = max(afm_revenue, min_revenue)

        return {
            "monthly_transactions": int(transaction_volume),
            "avg_transaction_usd": str(avg_transaction_size),
            "total_volume_usd": str(total_volume.quantize(Decimal("0.01"))),
            "afm_revenue_usd": str(afm_revenue.quantize(Decimal("0.01"))),
            "effective_rate_bps": self.commission_bps,
            "take_rate_pct": f"{self.commission_bps / 100:.2f}%",
        }


# Singleton
revenue_engine = AFMRevenueEngine()
