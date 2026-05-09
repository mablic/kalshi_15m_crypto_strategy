# pre strategy before the trade
import pandas as pd
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from models import GET_STRATEGY_MODEL, STRATEGY_LOADER

@dataclass
class MarketContext:
    close_price: float | None = None
    floor_strike: float | None = None

class CRYPTO_PRE_STRATEGY_BASE(ABC):
    def __init__(self, name: str, type: str):
        self.name = name
        self.type = type

    @abstractmethod
    def generate_signals(self, ctx: MarketContext) -> bool:
        pass


class CRYPTO_PRE_STRATEGY_EXPECTED_DISTANCE(CRYPTO_PRE_STRATEGY_BASE):
    def __init__(self, expected_distance: float = 0.0, strategy_name: str = 'yes'):
        super().__init__(name="EXPECTED_DISTANCE_PRE_STRATEGY", type=strategy_name)
        self.expected_distance = expected_distance

    def generate_signals(self, ctx: MarketContext) -> bool:
            # (df4['close'] - df4['floor_strike']) / df4['floor_strike']
        price_to_floor = (ctx.close_price - ctx.floor_strike) / ctx.floor_strike
        floor_dist = ctx.floor_strike
        if self.type == 'yes':
            model = STRATEGY_LOADER(
                model=GET_STRATEGY_MODEL(model_name="yes_dist_model"),
                model_data=pd.DataFrame(),
                x_features=["price_to_floor", "floor_dist"],
                y_feature="dist_pred",
                model_name="yes_dist_model",
            )
        else:
            model = STRATEGY_LOADER(
                model=GET_STRATEGY_MODEL(model_name="no_dist_model"),
                model_data=pd.DataFrame(),
                x_features=["price_to_floor", "floor_dist"],
                y_feature="dist_pred",
                model_name="no_dist_model",
            )
        # One row: dict-of-scalars is ambiguous; use a list of dicts (or `index=[0]`).
        X = pd.DataFrame(
            [{"price_to_floor": price_to_floor, "floor_dist": floor_dist}]
        )
        expected_distance = model.predict(X)
        
        # model.ingest(X)
        return expected_distance


if __name__ == "__main__":
    ctx = MarketContext(close_price=100.0, floor_strike=100.0)
    strategy = CRYPTO_PRE_STRATEGY_EXPECTED_DISTANCE(expected_distance=0.0, strategy_name='yes')
    print(f"yes expected distance: {strategy.generate_signals(ctx)}")
    strategy = CRYPTO_PRE_STRATEGY_EXPECTED_DISTANCE(expected_distance=0.0, strategy_name='no')
    print(f"no expected distance: {strategy.generate_signals(ctx)}")