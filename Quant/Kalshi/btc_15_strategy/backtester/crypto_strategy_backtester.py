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

class CRYPTO_STRATEGY_BACKTESTER:
    def __init__(self, strategy: CRYPTO_STRATEGY_MANAGER):
        self.strategy = strategy
        self.market_data: pd.DataFrame | None = None
        self.parameters_data: pd.DataFrame | None = None

    def add_parameters_data(self, parameters_data: pd.DataFrame):
        try:
            self.parameters_data = parameters_data
        except Exception as e:
            print(f"Error setting parameters data index: {e}")
            self.parameters_data = None

    def get_market_data(self, ticker: str):
        try:
            # read frin firebase
            # self.market_data = aggregate_snapshots_for_ticker(ticker)
            # read from api
            api_data = get_market_data_by_ticker_api(ticker)
            firebase_data = get_crypto_market_data_by_ticker(ticker)
            merged_data = pd.merge(api_data, firebase_data, on='datetime', how='left')
            merged_data = merged_data.dropna()
            self.market_data = merged_data
        except Exception as e:
            print(f"Error getting market data: {e}")
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
                    }
            except Exception:
                pass

            # yes_ask_price, no_ask_price, yes_bid_price, no_bid_price = self.get_price_from_order_book(row)
            yes_ask_price = row['yes_ask_low_dollar']
            no_ask_price = row['no_ask_low_dollar']
            yes_bid_price = row['yes_bid_high_dollar']
            no_bid_price = row['no_bid_high_dollar']
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
            with open("data.txt", 'a') as f:
                f.write(f"time is {entry_time}, yes_ask_price: {yes_ask_price}, yes_bid_price: {yes_bid_price}, no_ask_price: {no_ask_price}, no_bid_price: {no_bid_price}, yes_current_bid_price: {yes_bid_price}, no_current_bid_price: {no_bid_price}, distance: {distance}\n")
            # if i == 0:
            #     print(
            #         f"{'time':>6}  {'y_ask':>7}  {'y_bid':>7}  {'n_ask':>7}  {'n_bid':>7}  "
            #         f"{'dist':>8}"
            #     )
            #     print("  ".join("-" * w for w in (6, 7, 7, 7, 7, 8)))
            # print(
            #     f"{entry_time:6.2f}  {float(yes_ask_price):7.2f}  {float(yes_bid_price):7.2f}  "
            #     f"{float(no_ask_price):7.2f}  {float(no_bid_price):7.2f} {distance:8.2f}"
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
                current_yes_bid_price=yes_bid_price, current_no_bid_price=no_bid_price, trade_entry_time=trade_time, trade_exit_time=trade_time, parameters=parameters))
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
    df_merged['yes_dist'] = df_merged['close'] - df_merged['floor_strike']
    df_merged['log_return'] = np.log(df_merged['close'] / df_merged['close'].shift(1))
    df_merged['3m_log_return'] = df_merged['log_return'].rolling(3).std()
    df_merged['5m_log_return'] = df_merged['log_return'].rolling(5).std()
    df_merged['ma3'] = df_merged['close'].rolling(3).mean()
    df_merged['ma5'] = df_merged['close'].rolling(5).mean()
    df_merged['ma3_vs_strike'] = (df_merged['ma3'] - df_merged['floor_strike'])/df_merged['floor_strike'] * 100
    df_merged['ma5_vs_strike'] = (df_merged['ma5'] - df_merged['floor_strike'])/df_merged['floor_strike'] * 100
    df_merged['yes_dist_pct'] = df_merged['yes_dist'] / df_merged['floor_strike'] * 100
    df_merged['1m_yes_dist_momentum'] = df_merged['yes_dist'] - df_merged['yes_dist'].shift(1)
    df_merged['3m_yes_dist_momentum'] = df_merged['yes_dist'] - df_merged['yes_dist'].shift(3)
    df_merged['5m_yes_dist_momentum'] = df_merged['yes_dist'] - df_merged['yes_dist'].shift(5)
    df_merged['time_decay'] = np.where(df_merged.index.minute % 15 == 0, 0, 15 - df_merged.index.minute % 15)
    df_merged = df_merged.dropna()
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
    df_merged = backtest_bayesian_strategy_dataframe(crypto_at, tickers[0])
    # tickers = ["KXBTC15M-26APR091900-00"]

    best = None
    entry_distance = 300
    entry_price = 0.15
    exit_price = 0.5
    entry_time = 3
    stop_time = 1
    expected_distance = 300

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
    backtester.add_strategy(CRYPTO_STRATEGY_BAYESIAN_ENTRY(threshold=0.31))
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
        if '26MAY05' not in ticker and '26MAY06' not in ticker:
            continue
        backtester.get_market_data(ticker)
        with open("data.txt", "a") as f:
            result = backtester.run()
            if result['trade_side'] == 'yes':
                if result['pnl'] > 0:
                    count_win_yes += 1
                else:
                    count_loss_yes += 1
            else:
                if result['pnl'] > 0:
                    count_win_no += 1
                else:
                    count_loss_no += 1
            if result['pnl'] != 0:
                hour = result['trade_entry_time'].hour
                if hour not in hours.keys():
                    hours[hour] = 0
                hours[hour] += round(float(result['pnl']), 2)
                f.write(f"Ticker: {ticker} Result: {[key + ': ' + str(value) for key, value in result.items()]}\n")
    print(f"Cumulative PNL: {cum_pnl}")
    print(f"Yes winning Rate: {count_win_yes / (count_win_yes + count_loss_yes) * 100:.1f}%")
    print(f"No winning Rate: {count_win_no / (count_win_no + count_loss_no) * 100:.1f}%")
    print(f"Hours: {hours}")
    