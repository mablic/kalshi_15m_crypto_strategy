import sys
import joblib
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import warnings

warnings.filterwarnings("ignore")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MODELS_DIR = _REPO_ROOT / "models"
_RETRAIN_EVERY_N = 10


class STRATEGY_LOADER:
    def __init__(
        self,
        model: Any,
        model_data: pd.DataFrame,
        x_features: list[str],
        y_feature: str,
        model_name: str,
    ):
        self.model = model
        self.data_count = 0
        self.model_data = model_data.copy() if not model_data.empty else pd.DataFrame()
        self.x_features = x_features
        self.y_feature = y_feature
        self.model_name = model_name
        self._model_path = _MODELS_DIR / f"{model_name}.pkl"

    def _load_data(self, data: pd.DataFrame) -> None:
        missing = set(self.x_features + [self.y_feature]) - set(data.columns)
        if missing:
            raise ValueError(f"Batch missing columns: {missing}")
        self.model_data = pd.concat([self.model_data, data], ignore_index=True)
        self.data_count += len(data)
        if self.data_count >= _RETRAIN_EVERY_N:
            self.model.fit(
                self.model_data[self.x_features],
                self.model_data[self.y_feature],
            )
            self.data_count = 0
            # self.model_data = pd.DataFrame()
            _MODELS_DIR.mkdir(parents=True, exist_ok=True)
            joblib.dump(self.model, self._model_path)
            print(f"Model {self.model_name} retrained on last ≥{_RETRAIN_EVERY_N} rows → saved {self._model_path}")

    def ingest(self, batch: pd.DataFrame) -> None:
        """Append labeled rows (features + target). Retrains every N rows and saves the model."""
        self._load_data(batch)

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        """Inference only; does not update training buffer (use ``ingest`` for that)."""
        return self.model.predict(X[self.x_features])


    def get_total_data_count(self):
        return self.model_data.shape[0]

def GET_STRATEGY_MODEL(model_name: str):
    return joblib.load(_MODELS_DIR / f"{model_name}.pkl")


if __name__ == "__main__":
    sys.path.insert(0, str(_REPO_ROOT))

    # --- Mock stream: new labeled rows arrive in small batches (e.g. after each market close) ---
    rng = np.random.default_rng(42)
    n_stream = 24
    stream = pd.DataFrame(
        {
            "price_to_floor": rng.uniform(0.05, 0.95, n_stream),
            "floor_dist": rng.uniform(0.05, 0.95, n_stream),
        }
    )
    stream["dist_pred"] = (
        0.45 * stream["price_to_floor"] + 0.55 * stream["floor_dist"] + rng.normal(0, 0.03, n_stream)
    )

    loader = STRATEGY_LOADER(
        model=GET_STRATEGY_MODEL(model_name="yes_dist_model.pkl"),
        model_data=pd.DataFrame(),
        x_features=["price_to_floor", "floor_dist"],
        y_feature="dist_pred",
        model_name="yes_dist_model",
    )

    # Retrain fires when the rolling buffer hits N rows: e.g. 3+4+2=9, then the next +5 makes 14 → first fit.
    batch_sizes = [3, 4, 2, 5, 4, 6]  # sums to 24; retrains at 14 and 24 cumulative
    i = 0
    for k, bs in enumerate(batch_sizes):
        chunk = stream.iloc[i : i + bs].copy()
        i += bs
        before = loader.data_count
        print(
            f"  batch {k + 1}: +{len(chunk)} labeled rows "
            f"(buffer before ingest: {before} / {_RETRAIN_EVERY_N})"
            f"(total data count: {loader.get_total_data_count()})"
        )
        loader.ingest(chunk)
        print(f"           buffer after: {loader.data_count} (clears to 0 after each retrain)")

    X_test = pd.DataFrame({"price_to_floor": [0.12, 0.88], "floor_dist": [0.34, 0.67]})
    print("predict:", loader.predict(X_test))
