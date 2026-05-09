from datetime import datetime
from zoneinfo import ZoneInfo

_KALSHI_15M_MONTH = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)

_KALSHI_15M_MINUTE = (
    "15", "30", "45", "00",
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

        current_time = datetime.now(tz=ZoneInfo('America/New_York'))
        current_time = current_time.replace(second=0, microsecond=0)

        for series in self.series_list:
            ser = _kalshi_series_with_kx_prefix(series)
            minute = _KALSHI_15M_MINUTE[current_time.minute // 15]
            hour = current_time.hour if minute != "00" else current_time.hour + 1
            ticker = f"{ser}-{current_time.year % 100:02d}{_KALSHI_15M_MONTH[current_time.month - 1]}{current_time.day:02d}{hour:02d}{minute}-{minute}"
            self.ticker_list.append(ticker)

    def get_ticker_list(self):
        self._generate_ticker()
        return self.ticker_list

if __name__ == "__main__":
    generate_ticker = GENERATE_TICKER(series_list=["BTC15M"])
    print(generate_ticker.get_ticker_list())