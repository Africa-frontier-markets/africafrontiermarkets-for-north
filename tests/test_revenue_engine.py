from decimal import Decimal

from revenue_engine.revenue_engine import revenue_engine


def test_revenue_split_35_50_15():
    """Revenue split: 35% ads, 50% creator, 15% platform."""
    result = revenue_engine.calculate_split(Decimal("1000.00"))

    assert result.ads == Decimal("350.00")
    assert result.creator == Decimal("500.00")
    assert result.platform == Decimal("150.00")
    assert result.total == Decimal("1000.00")
