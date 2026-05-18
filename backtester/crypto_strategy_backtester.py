import sys
import argparse
import json
import numpy as np
import pandas as pd
from zoneinfo import ZoneInfo
from pathlib import Path
from datetime import datetime, timezone, timedelta
sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategy import *
from lib import *
from lib.trade_log import error, log

_BACKTESTER_LOG = Path(__file__).resolve().parent / "backtester.log"

# Per-ticker OHLCV+quotes from get_market_data(); reused across parameter grid to avoid 429s.
_BT_MARKET_DF_CACHE: dict[str, pd.DataFrame] = {}


def clear_backtester_market_cache() -> None:
    """Drop cached DataFrames from get_market_data (e.g. between full study runs)."""
    _BT_MARKET_DF_CACHE.clear()
def _backtest_result_log_line(result: dict) -> str:
    """Format trade result for BACKTEST log; pnl and dollar fields to 2 decimals."""
    parts = []
    for key, value in result.items():
        if value is None:
            parts.append(f"{key}: None")
            continue
        if key in ("pnl", "entry_price", "exit_price", "trade_lot"):
            try:
                parts.append(f"{key}: {float(value):.2f}")
            except (TypeError, ValueError):
                parts.append(f"{key}: {value}")
        else:
            parts.append(f"{key}: {value}")
    return "[" + ", ".join(parts) + "]"


class CRYPTO_STRATEGY_BACKTESTER:
    def __init__(self, strategy: CRYPTO_STRATEGY_MANAGER):
        self.strategy = strategy
        self.market_data: pd.DataFrame | None = None
        self.parameters_data: pd.DataFrame | None = None
        self._current_ticker: str | None = None

    def add_parameters_data(self, parameters_data: pd.DataFrame):
        try:
            self.parameters_data = parameters_data
        except Exception as e:
            error("Error setting parameters data index:", e, path=_BACKTESTER_LOG)
            self.parameters_data = None

    def get_market_data(self, ticker: str, *, use_cache: bool = True):
        """Load merged API+Firebase series for ``ticker``. Cached process-wide so each ticker is fetched once.

        Note: ``add_parameters_data(df_merged)`` is separate (Bayesian features, often built from one reference
        ticker). That does *not* replace this per-ticker price path — without cache, every grid cell × ticker
        would re-hit the APIs and trigger rate limits (429).
        """
        self._current_ticker = ticker
        if use_cache and ticker in _BT_MARKET_DF_CACHE:
            self.market_data = _BT_MARKET_DF_CACHE[ticker]
            return
        try:
            api_data = get_market_data_by_ticker_api(ticker)
            firebase_data = get_crypto_market_data_by_ticker(ticker)
            merged_data = pd.merge(api_data, firebase_data, on='datetime', how='left')
            merged_data = merged_data.dropna()
            _BT_MARKET_DF_CACHE[ticker] = merged_data
            self.market_data = merged_data
        except Exception as e:
            error("Error getting market data:", e, path=_BACKTESTER_LOG)
            self.market_data = None

    def get_strategy_data(self):
        return self.market_data

    def add_strategy(self, strategy: CRYPTO_STRATEGY_BASE):
        self.strategy.add_strategy(strategy)
    
    def set_strategy_ctx(self, ctx: MarketContext):
        self.ctx = ctx

    def set_trade_lot(self, lot_size: float):
        self.lot_size = lot_size

    def get_order_book_lot(self, order_book: list, lot_size: float) -> float | None:
        cum_lot = 0.0
        cum_price = 0.0
        if not order_book:
            return None
        for i in range(len(order_book)):
            cum_lot += float(order_book[i][1])
            cum_price += float(order_book[i][0])
            if cum_lot >= lot_size:
                break
        return round(cum_price / (i + 1), 2)

    def get_price_from_order_book(self, market_data: dict) -> float:

        yes_order_book_ask = market_data['yes_order_book_ask'] if market_data['yes_order_book_ask'] is not None else []
        no_order_book_ask = market_data['no_order_book_ask'] if market_data['no_order_book_ask'] is not None else []
        yes_order_book_bid = market_data['yes_order_book_bid'] if market_data['yes_order_book_bid'] is not None else []
        no_order_book_bid = market_data['no_order_book_bid'] if market_data['no_order_book_bid'] is not None else []

        yes_ask_price = self.get_order_book_lot(sorted(yes_order_book_ask, key=lambda x: -float(x[0]), reverse=True), self.lot_size)
        no_ask_price = self.get_order_book_lot(sorted(no_order_book_ask, key=lambda x: -float(x[0]), reverse=True), self.lot_size)
        yes_bid_price = self.get_order_book_lot(sorted(yes_order_book_bid, key=lambda x: float(x[0]), reverse=True), self.lot_size)
        no_bid_price = self.get_order_book_lot(sorted(no_order_book_bid, key=lambda x: float(x[0]), reverse=True), self.lot_size)

        if yes_ask_price is None:
            yes_ask_price = 1
        if no_ask_price is None:
            no_ask_price = 1
        if yes_bid_price is None:
            yes_bid_price = 0
        if no_bid_price is None:
            no_bid_price = 0

        return yes_ask_price, no_ask_price, yes_bid_price, no_bid_price

    def run(self):
        self.strategy.reset_trade()
        # None: load failed; empty: no rows to iterate (use .empty, not `if df` — non-empty is truthy for DataFrame)
        if self.market_data is None or self.market_data.empty:
            return self.strategy.get_trade_result()
        for i, (index, row) in enumerate(self.market_data.iterrows()):
            if self.strategy.is_trade_completed():
                break
            distance = round(float(0.0 if row['close'] is None else float(row['close'])) - float(0.0 if row['floor_strike'] is None else float(row['floor_strike'])), 2)
            entry_time = 15 - (row['datetime'].minute % 15) if row['datetime'].minute % 15 != 0 else 0
            trade_time = row['datetime']
            parameters = {}
            try:
                params_df = self.parameters_data
                if (
                    params_df is not None
                    and not params_df.empty
                    and trade_time in params_df.index
                ):
                    parameters = {
                        'ma3': params_df.loc[trade_time, 'ma3'],
                        'ma5': params_df.loc[trade_time, 'ma5'],
                        'ma3_vs_strike': params_df.loc[trade_time, 'ma3_vs_strike'],
                        'ma5_vs_strike': params_df.loc[trade_time, 'ma5_vs_strike'],
                        'yes_dist_pct': params_df.loc[trade_time, 'yes_dist_pct'],
                        '1m_yes_dist_momentum': params_df.loc[trade_time, '1m_yes_dist_momentum'],
                        '3m_yes_dist_momentum': params_df.loc[trade_time, '3m_yes_dist_momentum'],
                        '5m_yes_dist_momentum': params_df.loc[trade_time, '5m_yes_dist_momentum'],
                        'time_decay': params_df.loc[trade_time, 'time_decay'],
                        'log_return': params_df.loc[trade_time, 'log_return'],
                        '3m_log_return': params_df.loc[trade_time, '3m_log_return'],
                        '5m_log_return': params_df.loc[trade_time, '5m_log_return'],
                        'yes_dist': params_df.loc[trade_time, 'yes_dist'],
                        'yes_spread': params_df.loc[trade_time, 'yes_spread'],
                        'no_spread': params_df.loc[trade_time, 'no_spread'],
                        'volume_surge': params_df.loc[trade_time, 'volume_surge'],
                        'oi_change': params_df.loc[trade_time, 'oi_change'],
                        'minute': params_df.loc[trade_time, 'minute'],
                        'hour': params_df.loc[trade_time, 'hour'],
                    }
            except Exception:
                pass

            # yes_ask_price, no_ask_price, yes_bid_price, no_bid_price = self.get_price_from_order_book(row)
            yes_ask_price = row['yes_ask_low_dollar']
            no_ask_price = row['no_ask_low_dollar']
            yes_bid_price = row['yes_bid_high_dollar']
            no_bid_price = row['no_bid_high_dollar']
            row_ticker = row["ticker"] if "ticker" in row.index and pd.notna(row.get("ticker")) else self._current_ticker
            ticker_str = row_ticker if row_ticker is not None else "?"
            trade_side = None
            entry_price = None
            exit_price = None
            if self.strategy.get_trade_side() is None:
                entry_price = min(yes_ask_price, no_ask_price)
                exit_price = max(yes_bid_price, no_bid_price)
            else:
                if self.strategy.get_trade_side() == 'yes':
                    entry_price = yes_ask_price
                    exit_price = yes_bid_price
                else:
                    entry_price = no_ask_price
                    exit_price = no_bid_price
            # log(
            #     f"ticker={ticker_str} time is {entry_time}, yes_ask_price: {yes_ask_price}, yes_bid_price: {yes_bid_price}, "
            #     f"no_ask_price: {no_ask_price}, no_bid_price: {no_bid_price}, "
            #     f"yes_current_bid_price: {yes_bid_price}, no_current_bid_price: {no_bid_price}, distance: {distance}",
            #     category="BACKTEST",
            #     path=_BACKTESTER_LOG,
            # )
            if self.strategy.get_trade_side() is None:
                if yes_ask_price < no_ask_price:
                    trade_side = 'yes'
                else:
                    trade_side = 'no'
            else:
                trade_side = self.strategy.get_trade_side()
            self.set_strategy_ctx(MarketContext(entry_time=entry_time, stop_time=entry_time, entry_price=entry_price, 
                exit_price=exit_price, distance=distance, trade_side=trade_side, trade_lot=self.lot_size, 
                current_yes_bid_price=yes_bid_price, current_no_bid_price=no_bid_price, trade_entry_time=trade_time, trade_exit_time=trade_time, parameters=parameters, production=self.strategy.production))
            self.strategy.run_all_strategies(ctx=self.ctx)
        
        return self.strategy.get_trade_result()


def backtest_bayesian_strategy_dataframe(backtest_time: datetime, ticker: str):
    lookback_minutes = 8000
    series, event_dt = parse_kalshi_15m_event_ticker(ticker)
    crypto_at = backtest_time
    df_api = get_market_data_from_api(series, crypto_at, lookback_minutes)
    df_api = df_api.set_index('datetime')
    df_api.index = df_api.index.tz_convert('America/Chicago')
    df_api.sort_index(inplace=True)
    df_crypto = get_crypto_past_minutes(series, crypto_at, lookback_minutes)
    df_crypto['datetime'] = pd.to_datetime(df_crypto['datetime'])
    df_crypto['datetime'] = df_crypto['datetime'].dt.tz_convert('America/Chicago')
    df_crypto['datetime'] = df_crypto['datetime'].dt.floor('min')
    df_crypto = df_crypto.set_index('datetime')
    filter_timestamp = df_crypto[df_crypto.index.minute.isin([0,15,30,45])].index[0]
    df_crypto = df_crypto[df_crypto.index >= filter_timestamp]
    df_merged = df_crypto.join(df_api, how='left')
    df_calc = df_merged
    for side in ("yes", "no"):
        ask_c = f"{side}_ask_close_dollar"
        bid_c = f"{side}_bid_close_dollar"
        if ask_c not in df_calc.columns:
            if side == "yes":
                df_calc[ask_c] = df_calc["yes_ask_low_dollar"]
                df_calc[bid_c] = df_calc["yes_bid_high_dollar"]
            else:
                df_calc[ask_c] = df_calc["no_ask_low_dollar"]
                df_calc[bid_c] = df_calc["no_bid_high_dollar"]
    if "volume_fp" not in df_calc.columns:
        df_calc["volume_fp"] = np.nan
    if "open_interest_fp" not in df_calc.columns:
        df_calc["open_interest_fp"] = np.nan
    df_calc["volume_fp"] = pd.to_numeric(df_calc["volume_fp"], errors="coerce")
    df_calc["open_interest_fp"] = pd.to_numeric(df_calc["open_interest_fp"], errors="coerce")

    base_dist = df_calc["close"] - df_calc["floor_strike"]
    for side in ("yes", "no"):
        df_calc[f"{side}_dist"] = base_dist
        df_calc[f"{side}_dist_pct"] = df_calc[f"{side}_dist"] / df_calc["floor_strike"] * 100
        d = df_calc[f"{side}_dist"]
        df_calc[f"m1_{side}_dist_momentum"] = d - d.shift(1)
        df_calc[f"m3_{side}_dist_momentum"] = d - d.shift(3)
        df_calc[f"m5_{side}_dist_momentum"] = d - d.shift(5)
        df_calc[f"{side}_spread"] = (
            df_calc[f"{side}_ask_close_dollar"] - df_calc[f"{side}_bid_close_dollar"]
        )

    df_calc["log_return"] = np.log(df_calc["close"] / df_calc["close"].shift(1))
    df_calc["m3_log_return"] = df_calc["log_return"].rolling(3).std()
    df_calc["m5_log_return"] = df_calc["log_return"].rolling(5).std()
    df_calc["3m_log_return"] = df_calc["m3_log_return"]
    df_calc["5m_log_return"] = df_calc["m5_log_return"]
    df_calc["ma3"] = df_calc["close"].rolling(3).mean()
    df_calc["ma5"] = df_calc["close"].rolling(5).mean()
    df_calc["ma3_vs_strike"] = (df_calc["ma3"] - df_calc["floor_strike"]) / df_calc["floor_strike"] * 100
    df_calc["ma5_vs_strike"] = (df_calc["ma5"] - df_calc["floor_strike"]) / df_calc["floor_strike"] * 100
    df_calc["time_decay"] = np.where(df_calc.index.minute % 15 == 0, 0, 15 - df_calc.index.minute % 15)
    df_calc["hour"] = df_calc.index.hour
    df_calc["minute"] = df_calc.index.minute
    vol_mean5 = df_calc["volume_fp"].rolling(5).mean()
    df_calc["volume_surge"] = df_calc["volume_fp"] / vol_mean5.replace(0, np.nan)
    df_calc["oi_change"] = df_calc["open_interest_fp"] - df_calc["open_interest_fp"].shift(1)
    df_calc["1m_yes_dist_momentum"] = df_calc["m1_yes_dist_momentum"]
    df_calc["3m_yes_dist_momentum"] = df_calc["m3_yes_dist_momentum"]
    df_calc["5m_yes_dist_momentum"] = df_calc["m5_yes_dist_momentum"]
    df_merged = df_calc.dropna()
    return df_merged

def backtest_bayesian_strategy_get_market_data(backtest_time: datetime):
    df = backtest_bayesian_strategy_dataframe(backtest_time)
    return df.to_dict(orient='records')

if __name__ == "__main__":
    ticker = "KXBTC15M-26MAY040415-15"
    # crypto_at = datetime(2026, 5, 4, 3, 30, 0, tzinfo=ZoneInfo('America/Chicago'))
    crypto_at = datetime.now(tz=ZoneInfo('America/Chicago'))
    tickers = get_tickers_by_series("KXBTC15M")
    # crypto_at = datetime.now(tz=ZoneInfo('America/Chicago'))
    # tickers = ["KXBTC15M-26MAY160515-15"]
    df_merged = backtest_bayesian_strategy_dataframe(crypto_at, tickers[0])

    best = None
    entry_distance = 300
    entry_price = 0.10
    exit_price = 0.7
    entry_time = 3
    stop_time = 1
    expected_distance = 300

    for entry_time in range(3, 10):
        for stop_time in range(1, 3):
            strategy = CRYPTO_STRATEGY_MANAGER()
            backtester = CRYPTO_STRATEGY_BACKTESTER(strategy)
            backtester.set_trade_lot(1)
            backtester.add_parameters_data(df_merged)
            backtester.add_strategy(CRYPTO_STRATEGY_ENTRY_TIME(entry_time=entry_time))
            backtester.add_strategy(CRYPTO_STRATEGY_ENTRY_PRICE(entry_price=entry_price))
            backtester.add_strategy(CRYPTO_STRATEGY_EXIT_PRICE(exit_price=exit_price))
            backtester.add_strategy(CRYPTO_STRATEGY_ENTRY_DISTANCE(entry_distance=entry_distance))
            backtester.add_strategy(CRYPTO_STRATEGY_STOP_TIME(stop_time=stop_time))
            backtester.add_strategy(CRYPTO_DISTANCE_EXPECTED_DISTANCE(expected_distance=expected_distance))
            backtester.add_strategy(CRYPTO_STRATEGY_BAYESIAN_ENTRY(threshold=0.15))
            backtester.add_strategy(CRYPTO_STRATEGY_ENTRY_TRADE_SIDE(trade_side="yes"))
            backtester.strategy.set_minimum_entry_price(entry_price)
            backtester.strategy.set_maximum_exit_price(exit_price)
            # backtester.get_market_data(ticker)
            # result = backtester.run()
            # print(f"Ticker: {ticker} Result: {[key + ': ' + str(value) for key, value in result.items()]}\n")
            cum_pnl = 0.0
            count_win_yes, count_loss_yes, count_win_no, count_loss_no = 0, 0, 0, 0
            hours = {}
            for ticker in tickers:
                if '26MAY17' not in ticker and '26MAY16' not in ticker:
                    continue
                backtester.get_market_data(ticker)
                result = backtester.run()
                pnl = float(result["pnl"])
                cum_pnl += pnl
                if result['trade_side'] == 'yes':
                    if pnl > 0:
                        count_win_yes += 1
                    else:
                        count_loss_yes += 1
                else:
                    if pnl > 0:
                        count_win_no += 1
                    else:
                        count_loss_no += 1
                if pnl != 0:
                    t = result["trade_entry_time"]
                    hour_key = int(t.hour)
                    hours[hour_key] = round(hours.get(hour_key, 0) + pnl, 2)
                    log(
                        "Ticker:",
                        ticker,
                        "Result:",
                        _backtest_result_log_line(result),
                        category="BACKTEST",
                        path=_BACKTESTER_LOG,
                    )
            denom_yes = count_win_yes + count_loss_yes
            denom_no = count_win_no + count_loss_no
            log("Entry time:", entry_time, "Stop time:", stop_time, category="BACKTEST", path=_BACKTESTER_LOG)
            log("Cumulative PNL:", f"{cum_pnl:.2f}", category="BACKTEST", path=_BACKTESTER_LOG)
            log(
                "Yes winning rate:",
                f"{count_win_yes / denom_yes * 100 if denom_yes else 0:.1f}%",
                category="BACKTEST",
                path=_BACKTESTER_LOG,
            )
            log(
                "No winning rate:",
                f"{count_win_no / denom_no * 100 if denom_no else 0:.1f}%",
                category="BACKTEST",
                path=_BACKTESTER_LOG,
            )
            hours_fmt = ", ".join(f"{h:02d}h={pnl:.2f}" for h, pnl in sorted(hours.items()))
            log("Hours PNL by entry hour (0-23 local):", hours_fmt, category="BACKTEST", path=_BACKTESTER_LOG)
    