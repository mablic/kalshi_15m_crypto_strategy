from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

import pandas as pd

_CF_BENCHMARKS_INDICES_BASE = "https://www.cfbenchmarks.com/data/indices"

# Kalshi 15m crypto event series -> CF Benchmarks real-time index page (scraped __NEXT_DATA__ / rtis).
# BTC uses CME CF Bitcoin RTI ticker BRTI; others use *USD_RTI slug pattern on cfbenchmarks.com.
KALSHI_15M_CRYPTO_SERIES_CF_INDEX_URL: dict[str, str] = {
    "KXBTC15M": f"{_CF_BENCHMARKS_INDICES_BASE}/BRTI",
    "KXETH15M": f"{_CF_BENCHMARKS_INDICES_BASE}/ETHUSD_RTI",
    "KXSOL15M": f"{_CF_BENCHMARKS_INDICES_BASE}/SOLUSD_RTI",
    "KXXRP15M": f"{_CF_BENCHMARKS_INDICES_BASE}/XRPUSD_RTI",
    "KXBNB15M": f"{_CF_BENCHMARKS_INDICES_BASE}/BNBUSD_RTI",
    "KXDOGE15M": f"{_CF_BENCHMARKS_INDICES_BASE}/DOGEUSD_RTI",
    "KXHYPE15M": f"{_CF_BENCHMARKS_INDICES_BASE}/HYPEUSD_RTI",
}

BRTI_PAGE_URL = KALSHI_15M_CRYPTO_SERIES_CF_INDEX_URL["KXBTC15M"]

_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; btc_15_strategy/1.0; +https://github.com/)"
)


def normalize_kalshi_crypto_15m_series(series_or_ticker: str) -> str:
    """
    Normalize ``KXBTC15M``, ``BTC15M``, or full event ticker ``KXBTC15M-26MAY040415-15`` -> ``KXBTC15M``.
    """
    s = series_or_ticker.strip().upper()
    if "-" in s:
        s = s.split("-", 1)[0]
    if s.startswith("KX"):
        return s
    if s.endswith("15M"):
        return "KX" + s
    return s


def resolve_cfbenchmarks_url(series_or_ticker: str) -> str:
    """Return the CF Benchmarks index URL for a Kalshi 15m crypto series or event ticker."""
    key = normalize_kalshi_crypto_15m_series(series_or_ticker)
    try:
        return KALSHI_15M_CRYPTO_SERIES_CF_INDEX_URL[key]
    except KeyError as e:
        known = ", ".join(sorted(KALSHI_15M_CRYPTO_SERIES_CF_INDEX_URL))
        raise KeyError(
            f"No CF Benchmarks URL for series {key!r}. Expected one of: {known}"
        ) from e


def _http_get(url: str, timeout_s: float = 30.0) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _DEFAULT_USER_AGENT, "Accept": "text/html,application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read().decode("utf-8", "replace")


def _system_local_tzinfo() -> datetime.tzinfo:
    tz = datetime.now().astimezone().tzinfo
    if tz is None:
        raise RuntimeError("System local timezone is not available.")
    return tz


def _tz_convert_datetimes(series: pd.Series, use_local_time: bool) -> pd.Series:
    s = series
    if s.empty:
        return s
    if not use_local_time:
        return s
    if s.dt.tz is None:
        s = s.dt.tz_localize("UTC")
    return s.dt.tz_convert(_system_local_tzinfo())


def _tz_convert_index(index: pd.DatetimeIndex, use_local_time: bool) -> pd.DatetimeIndex:
    if index.empty:
        return index
    if not use_local_time:
        return index
    if index.tz is None:
        index = index.tz_localize("UTC")
    return index.tz_convert(_system_local_tzinfo())


def fetch_cfbenchmarks_page_props(url: str) -> dict[str, Any]:
    """Parse Next.js __NEXT_DATA__ JSON and return ``props['pageProps']``."""
    html = _http_get(url)
    m = re.search(
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        raise ValueError("No __NEXT_DATA__ script found; page structure may have changed.")
    payload = json.loads(m.group(1))
    try:
        return payload["props"]["pageProps"]
    except (KeyError, TypeError) as e:
        raise ValueError("Unexpected __NEXT_DATA__ shape.") from e


def _extract_rtis(page_props: dict[str, Any]) -> list[dict[str, Any]]:
    ic = page_props.get("indexConfig") or {}
    rtis = ic.get("rtis")
    if not isinstance(rtis, list):
        return []
    return rtis


def brti_ticks_dataframe(
    page_props: dict[str, Any] | None = None,
    *,
    page_url: str | None = None,
    use_local_time: bool = True,
) -> pd.DataFrame:
    """One row per embedded tick: datetime (local or UTC per flag), close price."""
    if page_props is None:
        if page_url is None:
            page_url = BRTI_PAGE_URL
        page_props = fetch_cfbenchmarks_page_props(page_url)
    rtis = _extract_rtis(page_props)
    if not rtis:
        return pd.DataFrame(columns=["datetime", "close"])
    rows = []
    for p in rtis:
        try:
            t_ms = int(p["time"])
            v = float(p["value"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append((pd.Timestamp(t_ms, unit="ms", tz="UTC"), v))
    if not rows:
        return pd.DataFrame(columns=["datetime", "close"])
    df = pd.DataFrame(rows, columns=["datetime", "close"])
    df = df.sort_values("datetime").drop_duplicates(subset=["datetime"])
    df = df.reset_index(drop=True)
    df["datetime"] = _tz_convert_datetimes(df["datetime"], use_local_time)
    return df


def get_cfbenchmarks_ohlc_dataframe(
    series_or_ticker: str,
    bar_freq: str | None = "1s",
    *,
    use_local_time: bool = True,
) -> pd.DataFrame:
    """
    Load CF Benchmarks real-time index ticks for a Kalshi 15m crypto series (embedded page data)
    and aggregate to OHLC.

    No ``datetime`` / lookback argument: the public HTML only includes the latest ~1h of
    per-second points baked into the page. For history you need a licensed CF Benchmarks feed.

    Index is datetime; use :func:`get_data_from_exchange_api` for a ``datetime`` column (e.g. ``trade.py``).
    """
    page_url = resolve_cfbenchmarks_url(series_or_ticker)
    page_props = fetch_cfbenchmarks_page_props(page_url)
    ticks = brti_ticks_dataframe(page_props, use_local_time=False)
    if ticks.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    s = ticks.set_index("datetime")["close"].sort_index()
    if bar_freq is None:
        ohlc = pd.DataFrame(
            {
                "open": s.values,
                "high": s.values,
                "low": s.values,
                "close": s.values,
            },
            index=s.index,
        )
    else:
        ohlc = s.resample(bar_freq).ohlc()
        ohlc = ohlc.dropna(how="any")
    ohlc.index = _tz_convert_index(ohlc.index, use_local_time)
    return ohlc


def get_brti_ohlc_dataframe(
    bar_freq: str | None = "1s",
    page_url: str | None = None,
    *,
    use_local_time: bool = True,
) -> pd.DataFrame:
    """Backward-compatible BTC (BRTI) OHLC; same as ``get_cfbenchmarks_ohlc_dataframe('KXBTC15M', ...)``."""
    url = page_url or BRTI_PAGE_URL
    page_props = fetch_cfbenchmarks_page_props(url)
    ticks = brti_ticks_dataframe(page_props, use_local_time=False)
    if ticks.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    s = ticks.set_index("datetime")["close"].sort_index()
    if bar_freq is None:
        ohlc = pd.DataFrame(
            {
                "open": s.values,
                "high": s.values,
                "low": s.values,
                "close": s.values,
            },
            index=s.index,
        )
    else:
        ohlc = s.resample(bar_freq).ohlc()
        ohlc = ohlc.dropna(how="any")
    ohlc.index = _tz_convert_index(ohlc.index, use_local_time)
    return ohlc


def get_data_from_exchange_api(
    series: str,
    bar_freq: str | None = "1min",
    use_local_time: bool = True,
) -> pd.DataFrame:
    ohlc = get_cfbenchmarks_ohlc_dataframe(
        series, bar_freq=bar_freq, use_local_time=use_local_time
    )
    if ohlc.empty:
        return pd.DataFrame(columns=["datetime", "open", "high", "low", "close"])
    out = ohlc.reset_index()
    if out.columns[0] != "datetime":
        out = out.rename(columns={out.columns[0]: "datetime"})
    return out


if __name__ == "__main__":
    try:
        s = "KXBTC15M-26MAY040415-15"
        print("URL for series prefix of", s, "->", resolve_cfbenchmarks_url(s))
        df = get_data_from_exchange_api(s, bar_freq="1min")
        print("get_data_from_exchange_api (1m) shape:", df.shape)
        print(df.head(3))
        print()
        df_eth = get_data_from_exchange_api("KXETH15M", bar_freq="1min")
        print("ETHUSD_RTI 1m head:\n", df_eth.head(2))
    except (urllib.error.URLError, ValueError, KeyError, json.JSONDecodeError) as e:
        print("Failed:", e)
