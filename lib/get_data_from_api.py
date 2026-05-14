import os
import sys
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

_EASTERN = ZoneInfo("America/New_York")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

sys.path.append(str(_PROJECT_ROOT))

from lib.get_data_from_exchange_api import normalize_kalshi_crypto_15m_series


def _resolve_env_path(path: str | None) -> Path | None:
    """Resolve paths from .env relative to the repo root (fixes Jupyter cwd != project root)."""
    if not path:
        return None
    p = Path(path).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (_PROJECT_ROOT / p).resolve()
from client import KalshiHttpClient, Environment
from cryptography.hazmat.primitives import serialization

_KALSHI_15M_MONTH = (
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
)


def kalshi_15m_event_tickers(
    series_ticker: str,
    start: datetime,
    end: datetime,
) -> list[str]:
    """Candidate tickers (15m grid). Many will not exist on the exchange; API returns 404 for those."""
    if start > end:
        return []
    t = start.astimezone(_EASTERN).replace(second=0, microsecond=0)
    t = t.replace(minute=(t.minute // 15) * 15)
    e = end.astimezone(_EASTERN).replace(second=0, microsecond=0)
    # Next 15m boundary after ``e``'s minute bucket (matches old intent). :45–:59 → :00 next hour.
    next_min = (e.minute // 15 + 1) * 15
    if next_min >= 60:
        e = (e + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    else:
        e = e.replace(minute=next_min, second=0, microsecond=0)
    ser = series_ticker.strip().upper()
    out: list[str] = []
    while t <= e:
        year = t.year % 100
        month = _KALSHI_15M_MONTH[t.month - 1]
        day = t.day
        hour = t.hour
        minute = t.minute
        # yy + MM + DD + HH + mm (UTC), then -mm (Python has no :yy / :MM; use % 100 and :02d)
        end_date = datetime(t.year, t.month, t.day, t.hour, t.minute, 0, 0, tzinfo=ZoneInfo('America/New_York'))
        start_date = end_date - timedelta(minutes=20)
        start_ts = int(start_date.timestamp())
        end_ts = int(end_date.timestamp())
        out.append(
            [f"{ser}-{year:02d}{month}{day:02d}{hour:02d}{minute:02d}-{minute:02d}", start_ts, end_ts]
        )
        t += timedelta(minutes=15)
    if out:
        return out[1:]
    return []


class GET_DATA_FROM_API:
    def __init__(self):
        load_dotenv(_PROJECT_ROOT / ".env")
        env = Environment.PROD  # toggle environment here
        KEYID = os.getenv("DEMO_KEYID") if env == Environment.DEMO else os.getenv("PROD_KEYID")
        keyfile_raw = os.getenv("DEMO_KEYFILE") if env == Environment.DEMO else os.getenv("PROD_KEYFILE")
        key_path = _resolve_env_path(keyfile_raw)

        try:
            if key_path is None:
                raise FileNotFoundError(
                    f"PROD_KEYFILE/DEMO_KEYFILE not set in {_PROJECT_ROOT / '.env'}"
                )
            if not key_path.is_file():
                raise FileNotFoundError(
                    f"Private key file not found at {key_path} "
                    f"(env had {keyfile_raw!r}; project root {_PROJECT_ROOT})"
                )
            with open(key_path, "rb") as key_file:
                private_key = serialization.load_pem_private_key(
                    key_file.read(),
                    password=None,
                )
        except FileNotFoundError:
            raise
        except Exception as e:
            raise Exception(f"Error loading private key: {str(e)}") from e

        self.client = KalshiHttpClient(
            key_id=KEYID,
            private_key=private_key,
            environment=env,
        )

    def _get_market_candlesticks_api(self, series_ticker: str, ticker: str, start_ts: int, end_ts: int):
        return self.client.get_market_candlesticks(
            series_ticker=series_ticker,
            ticker=ticker,
            start_ts=start_ts,
            end_ts=end_ts,
        )

    def _get_market_api(self, ticker: str):
        return self.client.get_market_ticker(
            ticker=ticker,
        )   

    def _standardize_data(self, results: list[dict], ticker: str, market_response: dict, candlesticks_response: dict):

        if isinstance(candlesticks_response, dict) and "candlesticks" in candlesticks_response:
            data = candlesticks_response["candlesticks"]
            for index, result in enumerate(data): 
                # Scalar → Timestamp: use .tz_convert, not .dt (Series accessor)
                data_time = pd.to_datetime(
                    result["end_period_ts"], unit="s", utc=True
                ).tz_convert("America/Chicago")
                yes_ask_open_dollar = round(float(result["yes_ask"]["open_dollars"]), 4)
                yes_ask_close_dollar = round(float(result["yes_ask"]["close_dollars"]), 4)
                yes_ask_high_dollar = round(float(result["yes_ask"]["high_dollars"]), 4)
                yes_ask_low_dollar = round(float(result["yes_ask"]["low_dollars"]), 4)
                yes_bid_open_dollar = round(float(result["yes_bid"]["open_dollars"]), 4)
                yes_bid_close_dollar = round(float(result["yes_bid"]["close_dollars"]), 4)
                yes_bid_high_dollar = round(float(result["yes_bid"]["high_dollars"]), 4)
                yes_bid_low_dollar = round(float(result["yes_bid"]["low_dollars"]), 4)
                no_ask_open_dollar = round(1 - yes_bid_close_dollar, 4)
                no_ask_close_dollar = round(1 - yes_bid_open_dollar, 4)
                no_ask_high_dollar = round(1 - yes_bid_high_dollar, 4)
                no_ask_low_dollar = round(1 - yes_bid_low_dollar, 4)
                no_bid_open_dollar = round(1 - yes_ask_close_dollar, 4)
                no_bid_close_dollar = round(1 - yes_ask_open_dollar, 4)
                no_bid_high_dollar = round(1 - yes_ask_high_dollar, 4)
                no_bid_low_dollar = round(1 - yes_ask_low_dollar, 4)
                volume_fp = round(float(result["volume_fp"]), 4)
                open_interest_fp = round(float(result["open_interest_fp"]), 4)
                tmp_dict = {
                    "datetime": data_time,
                    "ticker": ticker,
                    "floor_strike": round(float(market_response["market"]["floor_strike"]), 4),
                    "volume_fp": volume_fp,
                    "open_interest_fp": open_interest_fp,
                    "yes_ask_open_dollar": yes_ask_open_dollar,
                    "yes_ask_high_dollar": yes_ask_high_dollar,
                    "yes_ask_low_dollar": yes_ask_low_dollar,
                    "yes_ask_close_dollar": yes_ask_close_dollar,
                    "yes_bid_open_dollar": yes_bid_open_dollar,
                    "yes_bid_high_dollar": yes_bid_high_dollar,
                    "yes_bid_low_dollar": yes_bid_low_dollar,
                    "yes_bid_close_dollar": yes_bid_close_dollar,
                    "no_ask_open_dollar": no_ask_open_dollar,
                    "no_ask_high_dollar": no_ask_high_dollar,
                    "no_ask_low_dollar": no_ask_low_dollar,
                    "no_ask_close_dollar": no_ask_close_dollar,
                    "no_bid_open_dollar": no_bid_open_dollar,
                    "no_bid_high_dollar": no_bid_high_dollar,
                    "no_bid_low_dollar": no_bid_low_dollar,
                    "no_bid_close_dollar": no_bid_close_dollar,
                }
                results.append(tmp_dict)

    def get_market_candlesticks(self, series_ticker: str, end_time: datetime, lookback_minutes: int):
        series_ticker = normalize_kalshi_crypto_15m_series(series_ticker)
        start_time = end_time - timedelta(minutes=lookback_minutes)
        # start_time = start_time.replace(minute=(max(0, (start_time.minute // 15) - 1)) * 15 + 1, second=0, microsecond=0)
        # end_time = end_time.replace(minute=(max(0, end_time.minute // 15)) * 15, second=0, microsecond=0)

        tickers = kalshi_15m_event_tickers(series_ticker, start_time, end_time)
        results: list[dict] = []
        for ticker in tickers:
            try:
                candlesticks_response = self._get_market_candlesticks_api(
                    series_ticker=series_ticker,
                    ticker=ticker[0],
                    start_ts=ticker[1],
                    end_ts=ticker[2],
                )
                market_response = self._get_market_api(
                    ticker=ticker[0],
                )   
                self._standardize_data(results, ticker[0], market_response, candlesticks_response)
            except Exception as e:
                print(f"Error getting market candlesticks: {e}")
                continue

        df = pd.DataFrame(results)
        return df

    def get_market_candlesticks_by_ticker(self, ticker: str):
        parts = ticker.strip().upper().split("-", 1)
        series_ticker = normalize_kalshi_crypto_15m_series(parts[0])
        ticker = f"{series_ticker}-{parts[1]}" if len(parts) > 1 else series_ticker
        date_str = datetime.strptime(ticker.split("-")[1], "%y%b%d%H%M")
        date_str = date_str.replace(tzinfo=ZoneInfo('America/New_York'))
        start_time = date_str - timedelta(minutes=20)
        end_time = date_str
        candlesticks_response = self._get_market_candlesticks_api(series_ticker, ticker, int(start_time.timestamp()), int(end_time.timestamp()))
        market_response = self._get_market_api(ticker)
        results: list[dict] = []
        self._standardize_data(results, ticker, market_response, candlesticks_response)
        df = pd.DataFrame(results)
        if df.empty:
            return df
        df["datetime"] = pd.to_datetime(df["datetime"])
        df["datetime"] = df["datetime"].dt.tz_convert("America/Chicago")
        # df = df.set_index('datetime')
        df = df.sort_values(by="datetime")
        return df

def get_market_data_by_ticker_api(ticker: str):
    get_data_from_api = GET_DATA_FROM_API()
    return get_data_from_api.get_market_candlesticks_by_ticker(ticker)

def get_market_data_from_api(series_ticker: str, end_time: datetime, lookback_minutes: int):
    get_data_from_api = GET_DATA_FROM_API()
    return get_data_from_api.get_market_candlesticks(series_ticker, end_time, lookback_minutes)

if __name__ == "__main__":
    end = datetime(2026, 5, 4, 4, 30, 0)
    df = get_market_data_from_api(
        series_ticker="KXBTC15M",
        end_time=end,
        lookback_minutes=5,
    )
    print(df)
    # data = get_data_from_api.get_market_candlesticks_by_ticker("KXBTC15M-26APR221030-30")
    # print(data)
