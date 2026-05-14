import pandas as pd
import numpy as np
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
from lib.trade_log import log_bayesian, log_entry, log_exit, log_trade_enter, log_trade_exit
from strategy.models import GET_STRATEGY_MODEL, STRATEGY_LOADER
from strategy.crypto_bayesian_strategy import CRYPTO_BAYESIAN_STRATEGY


@dataclass
class MarketContext:

    entry_price: float | None = None
    exit_price: float | None = None
    distance: float | None = None
    entry_time: int | None = None
    entry_hour: int | None = None
    stop_time: int | None = None
    trade_side: str | None = None
    trade_lot: float | None = None
    current_yes_bid_price: float | None = None
    current_no_bid_price: float | None = None
    trade_entry_time: int | None = None
    trade_exit_time: int | None = None
    price_to_floor: float | None = None
    parameters: dict | None = None

class CRYPTO_STRATEGY_BASE(ABC):

    def __init__(self, name: str, type: str):
        self.name = name
        self.type = type

    @abstractmethod
    def generate_signals(self, ctx: MarketContext) -> bool:
        pass


class CRYPTO_STRATEGY_ENTRY_TIME(CRYPTO_STRATEGY_BASE):
    def __init__(self, entry_time: int = 0):
        super().__init__(name="ENTRY_TIME_CYCLE", type="buy")
        self.entry_time = entry_time

    def generate_signals(self, ctx: MarketContext) -> bool:
        if ctx.entry_time >= self.entry_time:
            return "buy"
        return "no"

class CRYPTO_STRATEGY_ENTRY_HOURS(CRYPTO_STRATEGY_BASE):
    def __init__(self, entry_hours: list[int]):
        super().__init__(name="ENTRY_HOUR_CYCLE", type="buy")
        self.entry_hours = entry_hours

    def generate_signals(self, ctx: MarketContext) -> bool:
        if ctx.entry_hour in self.entry_hours:
            return "buy"
        return "no"


class CRYPTO_STRATEGY_ENTRY_TRADE_SIDE(CRYPTO_STRATEGY_BASE):
    def __init__(self, trade_side: str = "yes"):
        super().__init__(name="ENTRY_TRADE_SIDE_CYCLE", type="buy")
        self.trade_side = trade_side

    def generate_signals(self, ctx: MarketContext) -> bool:
        if ctx.trade_side == self.trade_side:
            return "buy"
        return "no"


class CRYPTO_STRATEGY_STOP_TIME(CRYPTO_STRATEGY_BASE):
    def __init__(self, stop_time: int = 0):
        super().__init__(name="STOP_TIME_CYCLE", type="sell")
        self.stop_time = stop_time

    def generate_signals(self,ctx: MarketContext) -> bool:
        if ctx.stop_time <= self.stop_time:
            return "stop"
        return "no"


class CRYPTO_STRATEGY_ENTRY_DISTANCE(CRYPTO_STRATEGY_BASE):
    def __init__(self, entry_distance: float = 0.0):
        super().__init__(name="ENTRY_DISTANCE_CYCLE", type="buy")
        self.entry_distance = entry_distance

    def generate_signals(self, ctx: MarketContext) -> bool:
        d = ctx.distance
        if d is None:
            return "no"
        if abs(d) < self.entry_distance:
            return "buy"
        return "no"


class CRYPTO_DISTANCE_EXPECTED_DISTANCE(CRYPTO_STRATEGY_BASE):
    def __init__(self, expected_distance: float = 0.0):
        super().__init__(name="EXPECTED_DISTANCE_CYCLE", type="buy")
        self.expected_distance = expected_distance

    def generate_signals(self, ctx: MarketContext) -> bool:
        d = ctx.distance
        if d is None:
            return "no"
        if abs(d) < self.expected_distance:
            return "buy"
        return "no"


class CRYPTO_STRATEGY_ENTRY_PRICE(CRYPTO_STRATEGY_BASE):
    def __init__(self, entry_price: float = 0.0):
        super().__init__(name="ENTRY_PRICE_CYCLE", type="buy")
        self.entry_price = entry_price

    def generate_signals(self, ctx: MarketContext) -> bool:
        
        if ctx.entry_price is None or ctx.exit_price is None:
            return "no"
        if ctx.entry_price <= self.entry_price:
            return "buy"
        return "no"


class CRYPTO_STRATEGY_EXIT_PRICE(CRYPTO_STRATEGY_BASE):
    def __init__(self, exit_price: float = 0.0):
        super().__init__(name="EXIT_PRICE_CYCLE", type="sell")
        self.exit_price = exit_price

    def generate_signals(self, ctx: MarketContext) -> bool:
        if ctx.entry_price is None or ctx.exit_price is None:
            return "no"
        if ctx.exit_price >= self.exit_price:
            return "sell"
        return "no"

# Bayesian strategy
class CRYPTO_STRATEGY_BAYESIAN_ENTRY(CRYPTO_STRATEGY_BASE):
    def __init__(self, threshold: float = 0.5):
        super().__init__(name="BAYESIAN_ENTRY_CYCLE", type="buy")
        self.bayesian_strategy = CRYPTO_BAYESIAN_STRATEGY(model_name="yes_bayesian")
        self.threshold = threshold

    def generate_signals(self, ctx: MarketContext) -> bool:
        parameters_dict = self.bayesian_strategy.get_model_parameters()
        input_values = {}
        for feature, values in ctx.parameters.items():
            if feature in parameters_dict:
                input_values[feature] = values
        probability = self.bayesian_strategy.generate_probability(input_values=input_values)
        bayesian_ok = probability > self.threshold
        bayesian_signal = "buy" if bayesian_ok else "no"
        log_bayesian(bayesian_signal, probability, self.threshold)
        return bayesian_signal

class CRYPTO_STRATEGY_MANAGER:
    def __init__(self):
        self._buy_strategies: dict[str, CRYPTO_STRATEGY_BASE] = {}
        self._sell_strategies: dict[str, CRYPTO_STRATEGY_BASE] = {}
        self.in_trade = False
        self.trade_completed = False
        self.trade_direction = None
        self.trade_decision_type = 'No'
        self.trade_entry_price = None
        self.trade_exit_price = None
        self.trade_side = None
        self.trade_lot = None
        self.minimum_entry_price = None
        self.maximum_exit_price = None
        self.trade_entry_time = None
        self.trade_exit_time = None
        self.production = False
        self.buy_filled = False
        self.sell_filled = False

    def set_minimum_entry_price(self, price: float):
        self.minimum_entry_price = price

    def set_maximum_exit_price(self, price: float):
        self.maximum_exit_price = price

    def add_strategy(self, strategy: CRYPTO_STRATEGY_BASE) -> None:
        if strategy.type == "buy":
            self._buy_strategies[strategy.name] = strategy
        elif strategy.type == "sell":
            self._sell_strategies[strategy.name] = strategy
        # print(f"Added strategy: {strategy.name}")

    def set_to_production(self):
        self.production = True

    def remove_strategy(self, name: str) -> None:
        if name in self._strategies:
            del self._strategies[name]

    def get_strategy(self, name: str) -> CRYPTO_STRATEGY_BASE | None:
        return self._strategies.get(name)

    def get_all_strategies(self) -> list[CRYPTO_STRATEGY_BASE]:
        return list(self._strategies.values())

    def get_trade_side(self):
        if not self.in_trade:
            return None
        return self.trade_side

    def is_stop_exit(self, results_list: list[str]) -> bool:
        for result in results_list:
            if result.lower() == "stop":
                return True
        return False

    def is_sell_exit(self, results_list: list[str]) -> bool:
        for result in results_list:
            if result.lower() == "sell":
                return True
        return False

    def is_trade_completed(self) -> bool:
        if self.trade_completed:
            return True
        return False

    def set_buy_filled(self) -> bool:
        self.buy_filled = True
    
    def set_sell_filled(self) -> bool:
        self.sell_filled = True

    def run_all_strategies(self, ctx: MarketContext) -> dict[str, bool]:
        results = {}
        if self.trade_completed:
            self.trade_decision_type = 'completed'
            return
        if not self.in_trade:
            self.trade_direction = 'buy'
            for name, strategy in self._buy_strategies.items():
                signals = strategy.generate_signals(ctx)
                results[name] = signals
            entry_gate = "buy" if all(x.lower() == "buy" for x in results.values()) else "no"
            breakdown = " ".join(f"{k}={v}" for k, v in results.items())
            log_entry(entry_gate, breakdown)
            if entry_gate == "buy":
                self.in_trade = True
                self.trade_entry_time = ctx.trade_entry_time
                self.trade_side = ctx.trade_side
                self.trade_entry_price = max(self.minimum_entry_price, ctx.entry_price)
                self.trade_lot = ctx.trade_lot
                self.trade_decision_type = 'buy'
                log_trade_enter(ctx.trade_side, self.trade_entry_price, results)
        elif not self.production or (self.buy_filled and self.production):
            self.trade_direction = 'sell'
            for name, strategy in self._sell_strategies.items():
                signals = strategy.generate_signals(ctx)
                results[name] = signals
            sell_hit = self.is_sell_exit(results.values())
            stop_hit = self.is_stop_exit(results.values())
            if sell_hit:
                exit_gate = "sell"
            elif stop_hit:
                exit_gate = "stop"
            else:
                exit_gate = "hold"
            exit_breakdown = " ".join(f"{k}={v}" for k, v in results.items())
            log_exit(exit_gate, exit_breakdown)
            if sell_hit:
                self.trade_exit_price = min(self.maximum_exit_price, ctx.exit_price)
                self.trade_exit_time = ctx.trade_exit_time
                self.trade_decision_type = 'sell'
                log_trade_exit("sell", self.trade_side, self.trade_exit_price)
            elif stop_hit:
                self.trade_exit_time = ctx.trade_exit_time
                self.trade_decision_type = 'stop'
                if self.trade_side.lower() == 'yes':
                    self.trade_exit_price = ctx.current_yes_bid_price
                else:
                    self.trade_exit_price = ctx.current_no_bid_price
                log_trade_exit("stop", self.trade_side, self.trade_exit_price)
            if not self.production:
                self.trade_completed = True
            else:
                if self.production and self.buy_filled and self.sell_filled:
                    self.trade_completed = True
        else:
            exit_gate = "hold"
            exit_breakdown = " ".join(f"{k}={v}" for k, v in results.items())
            log_exit(exit_gate, exit_breakdown)
            
    def get_trade_decision(self):
        return self.trade_decision_type

    def get_trade_result(self):
        result = {
            'trade_entry_time': self.trade_entry_time,
            'trade_exit_time': self.trade_exit_time,
            'entry_price': self.trade_entry_price,
            'exit_price': self.trade_exit_price,
            'trade_side': self.trade_side,
            'trade_lot': self.trade_lot,
            'trade_direction': self.trade_direction,
        }
        pnl = 0 
        if not self.trade_entry_price:
            pnl = 0
        elif not self.trade_exit_price:
            pnl = -1 * round(float(self.trade_entry_price) * float(self.trade_lot), 2)
        else:
            pnl = round((float(self.trade_exit_price) - float(self.trade_entry_price)) * float(self.trade_lot), 2)
        result['pnl'] = pnl
        return result


    def reset_trade(self):
        self.in_trade = False
        self.trade_completed = False
        self.trade_direction = None
        self.trade_entry_price = None
        self.trade_exit_price = None
        self.trade_side = None
        self.trade_lot = None
        self.trade_decision_type = 'No'
        self.buy_filled = False
        self.sell_filled = False


if __name__ == "__main__":
    manager = CRYPTO_STRATEGY_MANAGER()
    manager.add_strategy(CRYPTO_STRATEGY_ENTRY_TIME())
    manager.add_strategy(CRYPTO_STRATEGY_ENTRY_PRICE(entry_price=4.0))
    manager.add_strategy(CRYPTO_STRATEGY_EXIT_PRICE(exit_price=4.0))
    manager.add_strategy(CRYPTO_STRATEGY_ENTRY_DISTANCE(entry_distance=0.05))
    ctx = MarketContext(current_price=3.55, distance=0.02)
    print(manager.run_all_strategies(ctx=ctx))
    print(manager.get_trade_result())