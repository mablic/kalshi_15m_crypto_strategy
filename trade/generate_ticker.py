from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_KALSHI_15M_MONTH = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)

def _kalshi_series_with_kx_prefix(series: str) -> str:
    """Kalshi API series tickers are e.g. ``KXBTC15M``, not ``BTC15M``."""
    s = series.strip().upper()
    if s.startswith("KX"):
        return s
    if s.endswith("15M"):
        return "KX" + s
    return s


class GENERATE_TICKER:
    def __init__(self, series_list: list[str]):
        self.series_list = series_list
        self.ticker_list = []

    def _generate_ticker(self):

        self.ticker_list = []
        ny_now = datetime.now(tz=ZoneInfo("America/New_York")).replace(second=0, microsecond=0)

        for series in self.series_list:
            ser = _kalshi_series_with_kx_prefix(series)
            q = ny_now.minute // 15
            # End of current quarter-hour in NY; :45–:59 → top of next hour (never ``hour == 24`` on same day).
            if q == 3:
                event_end = ny_now.replace(minute=45) + timedelta(minutes=15)
            else:
                event_end = ny_now.replace(minute=(q + 1) * 15)
            # event_utc = event_end.astimezone(timezone.utc)
            yy = event_end.year % 100
            mon = _KALSHI_15M_MONTH[event_end.month - 1]
            dd = event_end.day
            hh = event_end.hour
            mm = event_end.minute
            self.ticker_list.append(f"{ser}-{yy:02d}{mon}{dd:02d}{hh:02d}{mm:02d}-{mm:02d}")

    def get_ticker_list(self):
        self._generate_ticker()
        return self.ticker_list

if __name__ == "__main__":
    generate_ticker = GENERATE_TICKER(series_list=["BTC15M"])
    print(generate_ticker.get_ticker_list())