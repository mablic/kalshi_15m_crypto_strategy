import sys
import argparse
import json
import time
import numpy as np
import pandas as pd
import os
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from datetime import datetime, timezone, timedelta
sys.path.append(str(Path(__file__).resolve().parent.parent))
from strategy import *
from client import *
from lib import *
from generate_ticker import GENERATE_TICKER
from order_book import ORDER_BOOK_MANAGER, TICKER_ORDER_BOOK, ORDER
from lib.trade_log import dec2, error, info, log, log_market, log_sep

ENTRY_TIME = 4
ENTRY_PRICE = 0.1
EXIT_PRICE = 0.7
ENTRY_DISTANCE = 300
STOP_TIME = 0
THRESHOLD = 0.42
TRADE_SIDE = "yes"
TRADE_LOT = 1
WAIT_TIME = 30
ENTRY_HOURS = [6,7,8,9,12,13,14,15,16,17,18,19,20,21,22,23]

class TRADE:
    def __init__(self, series_list: list[str], client: KalshiHttpClient):
        self.series_list = series_list
        self.ticker_data = {}
        self.trade_lot = TRADE_LOT
        self.client = client
        self.strategy = CRYPTO_STRATEGY_MANAGER()
        self.order_book_managers = ORDER_BOOK_MANAGER()
        self._last_fill_time = int(datetime.now().timestamp())

    def _get_ticker_list(self):
        generate_ticker = GENERATE_TICKER(series_list=self.series_list)
        return generate_ticker.get_ticker_list()

    def _add_features_to_df(self, exchange_df: pd.DataFrame, api_df: pd.DataFrame):
        if exchange_df.empty or api_df.empty:
            return None

        exchange_df = exchange_df.copy()
        exchange_df["datetime"] = pd.to_datetime(exchange_df["datetime"])
        exchange_df["datetime"] = exchange_df["datetime"].dt.tz_convert("America/Chicago")
        exchange_df["datetime"] = exchange_df["datetime"].dt.floor("min")
        exchange_df = exchange_df.set_index("datetime")
        filter_timestamp = exchange_df[exchange_df.index.minute.isin([0, 15, 30, 45])].index[0]
        exchange_df = exchange_df[exchange_df.index >= filter_timestamp]

        api_df = api_df.copy()
        api_df["datetime"] = pd.to_datetime(api_df["datetime"])
        api_df["datetime"] = api_df["datetime"].dt.tz_convert("America/Chicago").dt.floor("min")
        api_df = api_df.set_index("datetime").sort_index()

        # Outer join keeps both sides; one-row live snapshot only aligns on one index label —
        # bfill/ffill spreads floor + quotes so rolling rows aren’t NaN and dropna doesn’t wipe history.
        df_merged = exchange_df.join(api_df, how="outer").sort_index()
        df_merged = df_merged.dropna(subset=["close"])
        kalshi_cols = [c for c in api_df.columns if c in df_merged.columns]
        df_merged[kalshi_cols] = df_merged[kalshi_cols].bfill().ffill()

        df_merged["yes_dist"] = df_merged["close"] - df_merged["floor_strike"]
        df_merged["log_return"] = np.log(df_merged["close"] / df_merged["close"].shift(1))
        df_merged["3m_log_return"] = df_merged["log_return"].rolling(3).std()
        df_merged["5m_log_return"] = df_merged["log_return"].rolling(5).std()
        df_merged["ma3"] = df_merged["close"].rolling(3).mean()
        df_merged["ma5"] = df_merged["close"].rolling(5).mean()
        df_merged["ma3_vs_strike"] = (df_merged["ma3"] - df_merged["floor_strike"]) / df_merged[
            "floor_strike"
        ] * 100
        df_merged["ma5_vs_strike"] = (df_merged["ma5"] - df_merged["floor_strike"]) / df_merged[
            "floor_strike"
        ] * 100
        df_merged["yes_dist_pct"] = df_merged["yes_dist"] / df_merged["floor_strike"] * 100
        df_merged["1m_yes_dist_momentum"] = df_merged["yes_dist"] - df_merged["yes_dist"].shift(1)
        df_merged["3m_yes_dist_momentum"] = df_merged["yes_dist"] - df_merged["yes_dist"].shift(3)
        df_merged["5m_yes_dist_momentum"] = df_merged["yes_dist"] - df_merged["yes_dist"].shift(5)
        df_merged["time_decay"] = np.where(
            df_merged.index.minute % 15 == 0, 0, 15 - df_merged.index.minute % 15
        )
        df_merged = df_merged.dropna()
        return df_merged

    def _set_strategy(self):
        self.strategy.add_strategy(CRYPTO_STRATEGY_ENTRY_TIME(entry_time=ENTRY_TIME))
        # self.strategy.add_strategy(CRYPTO_STRATEGY_ENTRY_PRICE(entry_price=ENTRY_PRICE))
        self.strategy.add_strategy(CRYPTO_STRATEGY_EXIT_PRICE(exit_price=EXIT_PRICE))
        self.strategy.add_strategy(CRYPTO_STRATEGY_ENTRY_DISTANCE(entry_distance=ENTRY_DISTANCE))
        self.strategy.add_strategy(CRYPTO_STRATEGY_STOP_TIME(stop_time=STOP_TIME))
        # self.strategy.add_strategy(CRYPTO_STRATEGY_BAYESIAN_ENTRY(threshold=THRESHOLD))
        self.strategy.add_strategy(CRYPTO_STRATEGY_ENTRY_TRADE_SIDE(trade_side=TRADE_SIDE))
        self.strategy.add_strategy(CRYPTO_STRATEGY_ENTRY_HOURS(entry_hours=ENTRY_HOURS))
        self.strategy.set_minimum_entry_price(ENTRY_PRICE)
        self.strategy.set_maximum_exit_price(EXIT_PRICE)
        self.strategy.set_to_production()

    def get_filled_orders_from_api(self, ticker: str):
        try:
            filled_orders = self.client.get_fills(min_ts=self._last_fill_time)['fills']
            self._last_fill_time = int(datetime.now().timestamp())
            result = []
            for f in filled_orders:
                if f['ticker'] == ticker:
                    filled_order = ORDER(
                        order_id=f.get('order_id'),
                        ticker=f.get('ticker'),
                        symbol=f.get('ticker'),
                        order_date=f.get('created_time'),
                        order_type='fill',
                        order_execution_type=None,
                        action=f.get('action'),
                        side=f.get('side'),
                        quantity=f.get('count_fp'),
                        remaining_quantity=f.get('count_fp'),
                        entry_price=f.get('yes_price_dollars') if f.get('side') == 'yes' else f.get('no_price_dollars'),
                        expected_exit_price=EXIT_PRICE,
                        price=f.get('yes_price_dollars') if f.get('side') == 'yes' else f.get('no_price_dollars'),
                        created_at=f.get('created_time'),
                        last_updated_at=datetime.now(tz=ZoneInfo('America/Chicago')).strftime('%Y-%m-%d %H:%M:%S'),
                        trade_type='crypto',
                        fill_id=f.get('fill_id'),
                    )
                    result.append(filled_order)
            return result
        except Exception as e:
            error("Error getting filled orders from API:", e)
        return None

    def get_position_from_api(self, ticker: str):
        try:
            positions = self.client.get_positions()['market_positions']
            for p in positions:
                if p['ticker'] == ticker:
                    return p
        except Exception as e:
            error("Error getting position from API:", e)
        return None

    def get_current_market_data_from_api(
        self,
        ticker: str,
        *,
        snapshot_bar_time: datetime | None = None,
    ):

        try:
            market_data = self.client.get_market_ticker(ticker=ticker)['market']
            order_book = self.get_order_book_by_ticker(ticker)
            yes_ask_low = order_book['yes_ask_low_dollar']
            yes_bid_high = order_book['yes_bid_high_dollar']
            no_ask_low = order_book['no_ask_low_dollar']
            no_bid_high = order_book['no_bid_high_dollar']

            chi = ZoneInfo("America/Chicago")
            if snapshot_bar_time is not None:
                bar_time = snapshot_bar_time
                if bar_time.tzinfo is None:
                    bar_time = bar_time.replace(tzinfo=chi)
                else:
                    bar_time = bar_time.astimezone(chi)
            else:
                now = datetime.now(tz=chi)
                # Last *completed* minute aligns with typical 1m OHLC last row
                bar_time = now.replace(second=0, microsecond=0) - timedelta(minutes=1)
            bar_time = bar_time.replace(second=0, microsecond=0)

            market_df = pd.DataFrame({
                'datetime': bar_time,
                'ticker': market_data['ticker'],
                'floor_strike': market_data['floor_strike'],
                "yes_ask_low_dollar": yes_ask_low,
                "yes_bid_high_dollar": yes_bid_high,
                "no_ask_low_dollar": no_ask_low,
                "no_bid_high_dollar": no_bid_high,
            }, index=[0])
            # market_df = market_df.set_index('datetime')
            return market_df
        except Exception as e:
            print(f"Error getting current market data from API: {e}")
            return None

    def set_strategy_ctx(self, ctx: MarketContext):
        self.ctx = ctx

    def initialize_dataframes(self):
        crypto_at = timedelta(minutes=15) + datetime.now(tz=ZoneInfo('America/Chicago'))
        ticker_list = self._get_ticker_list()
        for ticker in ticker_list:
            series, _ = parse_kalshi_15m_event_ticker(ticker)
            series = normalize_kalshi_crypto_15m_series(series)
            exchange_df = get_data_from_exchange_api(series, bar_freq="1min")
            api_df = get_market_data_from_api(series, crypto_at, 60)
            merged_df = self._add_features_to_df(exchange_df, api_df)
            if merged_df is not None:
                self.ticker_data[series] = merged_df

    def read_new_data(self, lookback_minutes: int = 5):
        ticker_list = self._get_ticker_list()
        for ticker in ticker_list:
            series, _ = parse_kalshi_15m_event_ticker(ticker)
            series = normalize_kalshi_crypto_15m_series(series)
            exchange_df = get_data_from_exchange_api(series, bar_freq="1min")
            if exchange_df.empty:
                continue
            last_bar = pd.to_datetime(exchange_df["datetime"].iloc[-1]).tz_convert(
                "America/Chicago"
            )
            api_df = self.get_current_market_data_from_api(
                ticker, snapshot_bar_time=last_bar.to_pydatetime()
            )
            if api_df is None or api_df.empty:
                continue
            # api_df = get_market_data_from_api(series, crypto_at, lookback_minutes)
            merged_df = self._add_features_to_df(exchange_df, api_df)
            if merged_df is not None and not merged_df.empty:
                # Rebuild from fresh CF OHLC + latest order book; avoids stale tail stuck after bad concat/join.
                self.ticker_data[series] = merged_df

    def get_open_order_by_ticker(self, ticker: str):
        try:
            result = []
            orders = self.client.get_open_orders()['orders']
            for o in orders:
                if o['ticker'] == ticker:
                    open_order = ORDER(
                        order_id=o.get('order_id'),
                        ticker=o.get('ticker'),
                        symbol=o.get('ticker'),
                        order_date=o.get('created_time'),
                        order_type='order',
                        order_execution_type=None,
                        action=o.get('action'),
                        side=o.get('side'),
                        quantity=o.get('initial_count_fp'),
                        remaining_quantity=o.get('remaining_count_fp'),
                        entry_price=o.get('yes_price_dollars') if o.get('side') == 'yes' else o.get('no_price_dollars'),
                        expected_exit_price=EXIT_PRICE,
                        price=o.get('yes_price_dollars') if o.get('side') == 'yes' else o.get('no_price_dollars'),
                        created_at=o.get('created_time'),
                        last_updated_at=o.get('last_update_time'),
                        trade_type='crypto',
                        fill_id=None,
                    )
                    result.append(open_order)
            return result
        except Exception as e:
            error("Error getting order by ticker from API:", e)
        return None

    def get_order_book_by_ticker(self, ticker: str):
        try:
            market_order_book = self.client.get_market_ticker_order_book(ticker=ticker)['orderbook_fp']
            yes_bid_orders = [float(x[0]) for x in market_order_book['yes_dollars']]
            no_bid_orders = [float(x[0]) for x in market_order_book['no_dollars']]
            yes_ask_orders = [1 - x for x in no_bid_orders]
            no_ask_orders = [1 - x for x in yes_bid_orders]

            # Empty side → no min/max; use bounds so strategy doesn't crash (wide book).
            yes_ask_low = min(min(yes_ask_orders), 0.99) if yes_ask_orders else 0.99
            yes_bid_high = max(max(yes_bid_orders), 0.01) if yes_bid_orders else 0.01
            no_ask_low = min(min(no_ask_orders), 0.99) if no_ask_orders else 0.99
            no_bid_high = max(max(no_bid_orders), 0.01) if no_bid_orders else 0.01

            return {
                'yes_ask_low_dollar': yes_ask_low,
                'yes_bid_high_dollar': yes_bid_high,
                'no_ask_low_dollar': no_ask_low,
                'no_bid_high_dollar': no_bid_high,
            }
        except Exception as e:
            error("Error getting order book by ticker from API:", e)
        return None

    def get_trade_decision_by_ticker(self, ticker: str, row: pd.Series):
        try:
            trade_time = row.index
            chi = ZoneInfo("America/Chicago")
            clock_min = datetime.now(tz=chi).replace(second=0, microsecond=0)
            entry_time = 0 if clock_min.minute % 15 == 0 else 15 - clock_min.minute % 15
            distance = round(
                float(0.0 if row["close"] is None else float(row["close"]))
                - float(0.0 if row["floor_strike"] is None else float(row["floor_strike"])),
                2,
            )
            parameters = {
                'ma3': row['ma3'],
                'ma5': row['ma5'],
                'ma3_vs_strike': row['ma3_vs_strike'],
                'ma5_vs_strike': row['ma5_vs_strike'],
                'yes_dist_pct': row['yes_dist_pct'],
                '1m_yes_dist_momentum': row['1m_yes_dist_momentum'],
                '3m_yes_dist_momentum': row['3m_yes_dist_momentum'],
                '5m_yes_dist_momentum': row['5m_yes_dist_momentum'],
                'time_decay': entry_time,
                'log_return': row['log_return'],
                '3m_log_return': row['3m_log_return'],
                '5m_log_return': row['5m_log_return'],
                'yes_dist': row['yes_dist'],
            }

            # yes_ask_price, no_ask_price, yes_bid_price, no_bid_price = self.get_price_from_order_book(row)
            yes_ask_price = row['yes_ask_low_dollar']
            no_ask_price = row['no_ask_low_dollar']
            yes_bid_price = row['yes_bid_high_dollar']
            no_bid_price = row['no_bid_high_dollar']
            trade_side = None
            entry_price = None
            exit_price = None
            entry_hour = datetime.now(tz=ZoneInfo('America/Chicago')).hour
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
            log_sep()
            log_market(
                clock_min,
                ticker,
                entry_time,
                yes_ask_price,
                yes_bid_price,
                no_ask_price,
                no_bid_price,
                distance,
            )
            if self.strategy.get_trade_side() is None:
                # if yes_ask_price < no_ask_price:
                trade_side = 'yes'
                # else:
                #     trade_side = 'no'
            else:
                trade_side = self.strategy.get_trade_side()
            self.set_strategy_ctx(MarketContext(entry_time=entry_time, stop_time=entry_time, entry_price=entry_price, 
                exit_price=exit_price, distance=distance, trade_side=trade_side, trade_lot=self.trade_lot, 
                current_yes_bid_price=yes_bid_price, current_no_bid_price=no_bid_price, trade_entry_time=trade_time, trade_exit_time=trade_time, parameters=parameters, entry_hour=entry_hour))
            self.strategy.run_all_strategies(ctx=self.ctx)
            trade_decision = self.strategy.get_trade_decision()
            return trade_decision
        except Exception as e:
            error("Error getting trade decision by ticker:", e)
        return None 

    def _update_open_order_book(self, ticker: str, open_order: dict, open_filled_orders: list):
        """Sync local order book with API (placeholder)."""
        pass

    def run(self):
        self._set_strategy()
        self.initialize_dataframes()
        while True:
            self.read_new_data()
            for series_ticker in self.ticker_data.keys():
                last_df = self.ticker_data[series_ticker].tail(1)
                row = last_df.iloc[0]  
                ticker = row['ticker'] 
                open_order = self.get_open_order_by_ticker(ticker)
                open_filled_orders = self.get_filled_orders_from_api(ticker)
                for order in open_filled_orders:
                    if order.action == 'buy':
                        self.strategy.set_buy_filled()
                        break
                    elif order.action == 'sell':
                        self.strategy.set_sell_filled()
                        break
                # if nothing is open, create or filled
                if not self.order_book_managers.check_ticker_in_order_book_manager(ticker):
                    log_sep()
                    log("ADD_ORDER_BOOK_MANAGER", ticker, category="TRADE")
                    self.strategy.reset_trade()
                    self.order_book_managers.clean_order_book_manager()
                    self.order_book_managers.add_order_book_manager(TICKER_ORDER_BOOK(ticker))

                trade_decision = self.get_trade_decision_by_ticker(ticker, row)
                if trade_decision == 'completed':
                    continue
                # trade_decision = 'buy'
                order_book = self.get_order_book_by_ticker(ticker)
                if trade_decision == 'buy' and not open_order and not self.order_book_managers.get_order_book_manager(ticker).is_in_trade():
                    buy_order = ORDER(
                        order_id=None,
                        ticker=ticker,
                        symbol=ticker,
                        order_date=datetime.now(tz=ZoneInfo('America/Chicago')).strftime('%Y-%m-%d %H:%M:%S'),
                        order_type=series_ticker,
                        order_execution_type='resting',
                        action='buy',
                        side='yes',
                        quantity=self.trade_lot,
                        remaining_quantity=self.trade_lot,
                        entry_price=min(order_book['yes_ask_low_dollar'], ENTRY_PRICE) ,
                        expected_exit_price=EXIT_PRICE,
                        price=ENTRY_PRICE,
                        created_at=datetime.now(tz=ZoneInfo('America/Chicago')).strftime('%Y-%m-%d %H:%M:%S'),
                        last_updated_at=datetime.now(tz=ZoneInfo('America/Chicago')).strftime('%Y-%m-%d %H:%M:%S'),
                        trade_type='buy',
                        fill_id=None,
                    )
                    self.order_book_managers.get_order_book_manager(ticker).add_to_buy_orders(buy_order)
                else:
                    self.order_book_managers.get_order_book_manager(ticker).order_decision(open_order, open_filled_orders)
                to_be_trade_list = self.order_book_managers.get_order_book_manager(ticker).get_to_be_trade_list()
                for to_be_trade_order in to_be_trade_list:
                    if to_be_trade_order.action == 'buy':
                        log("PLACE_BUY", to_be_trade_order.ticker, to_be_trade_order.side, "n=", to_be_trade_order.remaining_quantity, "yes$=", dec2(to_be_trade_order.entry_price), category="TRADE")
                        self.client.create_open_order(
                            ticker=to_be_trade_order.ticker,
                            side=to_be_trade_order.side,
                            action=to_be_trade_order.action,
                            count=to_be_trade_order.remaining_quantity,
                            yes_price_dollars=to_be_trade_order.entry_price,
                        )
                    else:
                        if trade_decision == 'stop':
                            ypx = max(order_book['yes_bid_high_dollar'], 0.01)
                        else:
                            ypx = max(order_book['yes_bid_high_dollar'], to_be_trade_order.entry_price, 0.01) 
                        log("PLACE_SELL", to_be_trade_order.ticker, to_be_trade_order.side, "n=", to_be_trade_order.remaining_quantity, "yes$=", dec2(ypx), "cancel_id=", to_be_trade_order.order_id, category="TRADE")
                        if to_be_trade_order.order_id is not None:
                            self.client.cancel_open_order(order_id=to_be_trade_order.order_id)
                        self.client.create_open_order(
                            ticker=to_be_trade_order.ticker,
                            side=to_be_trade_order.side,
                            action=to_be_trade_order.action,
                            count=to_be_trade_order.remaining_quantity,
                            yes_price_dollars=ypx,
                        )
                    self.order_book_managers.get_order_book_manager(ticker).clear_to_be_trade_list()
            time.sleep(WAIT_TIME)

if __name__ == "__main__":
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_PROJECT_ROOT / ".env")
    env = Environment.PROD 
    KEYID = os.getenv("DEMO_KEYID") if env == Environment.DEMO else os.getenv("PROD_KEYID")
    key_raw = os.getenv("DEMO_KEYFILE") if env == Environment.DEMO else os.getenv("PROD_KEYFILE")
    try:
        if key_raw is None:
            raise FileNotFoundError(
                f"PROD_KEYFILE/DEMO_KEYFILE not set in {_PROJECT_ROOT / '.env'}"
            )
        key_path = Path(key_raw).expanduser()
        if not key_path.is_absolute():
            key_path = (_PROJECT_ROOT / key_path).resolve()
        else:
            key_path = key_path.resolve()
        if not key_path.is_file():
            raise FileNotFoundError(
                f"Private key file not found at {key_path} (env had {key_raw!r})"
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
    client = KalshiHttpClient(
        key_id=KEYID,
        private_key=private_key,
        environment=env,
    )
    info("TRADE start env=", env, "series=BTC15M", "wait_s=", WAIT_TIME)
    trade = TRADE(series_list=["BTC15M"], client=client)  
    trade.run()