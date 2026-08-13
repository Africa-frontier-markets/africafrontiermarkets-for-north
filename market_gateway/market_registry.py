"""Market registry with ZoneInfo and weekend detection. V45 Correction."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config.exceptions import MarketClosedError, NotFoundError
from market_gateway.models import Market, MarketStatus


class MarketRegistry:
    WEEKEND_DEFAULT = {6, 7}

    def __init__(self):
        self._markets = {}

    def register(self, market: Market) -> None:
        self._markets[market.code] = market

    def get_market(self, code: str) -> Market:
        if code not in self._markets:
            raise NotFoundError(f"Market {code} not found")
        return self._markets[code]

    def list_markets(self, country: str | None = None, active_only: bool = True) -> list[Market]:
        markets = list(self._markets.values())
        if active_only:
            markets = [m for m in markets if m.is_active]
        if country:
            markets = [m for m in markets if m.country == country]
        return markets

    def is_open(self, code: str, at: datetime | None = None) -> bool:
        market = self.get_market(code)
        check_time = at or datetime.now(timezone.utc)

        try:
            market_tz = ZoneInfo(market.timezone)
        except ZoneInfo.NotFoundError:
            market_tz = timezone.utc

        local_time = check_time.astimezone(market_tz)

        weekend_days = self.WEEKEND_DEFAULT
        if market.weekend_days:
            try:
                weekend_days = {int(d) for d in market.weekend_days.split(",")}
            except ValueError:
                pass

        if local_time.isoweekday() in weekend_days:
            return False

        try:
            open_h, open_m = map(int, market.open_time.split(":"))
            close_h, close_m = map(int, market.close_time.split(":"))
        except ValueError:
            return True

        current_minutes = local_time.hour * 60 + local_time.minute
        open_minutes = open_h * 60 + open_m
        close_minutes = close_h * 60 + close_m

        if close_minutes < open_minutes:
            return current_minutes >= open_minutes or current_minutes <= close_minutes
        else:
            return open_minutes <= current_minutes <= close_minutes

    def assert_open(self, code: str, at: datetime | None = None) -> None:
        if not self.is_open(code, at):
            market = self.get_market(code)
            raise MarketClosedError(
                f"Market {market.name} ({code}) is currently closed. "
                f"Trading hours: {market.open_time}-{market.close_time} {market.timezone}"
            )


market_registry = MarketRegistry()
