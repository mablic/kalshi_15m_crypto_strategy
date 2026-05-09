import pandas as pd
import numpy as np
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).resolve().parent.parent))
from lib import *
from datetime import datetime, timezone

class CRYPTO_DATA_BASE:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        pass

    def prepare_data(self, dataframe: pd.DataFrame):
        try:
            self.df = dataframe 
            self.df['datetime'] = pd.to_datetime(self.df['datetime'])
            self.df['datetime'] = self.df['datetime'].dt.tz_convert('America/Chicago')
            self.df = self.df[self.df[self.df['datetime'].dt.minute.isin([0,15,30,45])].iloc[0].name:]
            self.df = self.df.set_index('datetime')
            self.df = self.df.sort_index()
            self.df_15min = self.df.resample('15min', closed='left', label='left').agg({'open':'first','high':'max','low':'min','close':'last'})
            self.df_15min['log_return'] = np.log(self.df_15min['close']).diff()
            self.df_15min.dropna(inplace=True)
            return self.df_15min
        except Exception as e:
            print(f"Error preparing data: {e}")
            return None

if __name__ == "__main__":
    example_ticker = "KXBTC15M-26APR170400-00"
    lookback_minutes = 50000
    series, event_dt = parse_kalshi_15m_event_ticker(example_ticker)
    dt_only = get_ticker_datetime(example_ticker)
    crypto_at = datetime.now(timezone.utc)
    df = get_crypto_past_minutes(series, crypto_at, lookback_minutes)
    df2 = get_kalshi_snapshots_for_series_range(series, crypto_at, lookback_minutes)
    print(df2)
    # crypto_data_base = CRYPTO_DATA_BASE()
    # df_15min = crypto_data_base.prepare_data(df)
    # print(df_15min)