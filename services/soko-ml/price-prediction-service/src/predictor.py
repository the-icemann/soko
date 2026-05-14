import os
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger()

SUPPORTED_MARKETS = [
    "Kisenyi_Kampala", "Gulu", "Mbarara", "Mbale", "Lira", "Masaka"
]

SUPPORTED_CROPS = [
    "maize_grain", "yellow_beans", "irish_potatoes", "tomatoes",
    "matoke", "cassava_chips", "sorghum", "millet"
]

# Base wholesale prices per kg in UGX — FarmGain Africa Uganda approximations
BASE_PRICES_UGX: dict[str, dict[str, float]] = {
    "maize_grain":    {"Kisenyi_Kampala": 1300, "Gulu": 1100, "Mbarara": 1250, "Mbale": 1150, "Lira": 1050, "Masaka": 1200},
    "yellow_beans":   {"Kisenyi_Kampala": 3200, "Gulu": 2800, "Mbarara": 3000, "Mbale": 2900, "Lira": 2700, "Masaka": 3100},
    "irish_potatoes": {"Kisenyi_Kampala": 800,  "Gulu": 700,  "Mbarara": 850,  "Mbale": 750,  "Lira": 680,  "Masaka": 820},
    "tomatoes":       {"Kisenyi_Kampala": 1500, "Gulu": 1200, "Mbarara": 1400, "Mbale": 1300, "Lira": 1100, "Masaka": 1350},
    "matoke":         {"Kisenyi_Kampala": 600,  "Gulu": 500,  "Mbarara": 650,  "Mbale": 550,  "Lira": 480,  "Masaka": 620},
    "cassava_chips":  {"Kisenyi_Kampala": 950,  "Gulu": 850,  "Mbarara": 900,  "Mbale": 880,  "Lira": 820,  "Masaka": 920},
    "sorghum":        {"Kisenyi_Kampala": 1100, "Gulu": 950,  "Mbarara": 1050, "Mbale": 1000, "Lira": 920,  "Masaka": 1080},
    "millet":         {"Kisenyi_Kampala": 2200, "Gulu": 1900, "Mbarara": 2100, "Mbale": 2000, "Lira": 1850, "Masaka": 2150},
}


class ModelRegistry:
    """Loads and caches Prophet .pkl models from MODEL_DIR at startup."""

    def __init__(self, model_dir: str):
        self.model_dir = Path(model_dir)
        self._models: dict[str, object] = {}

    def load_all(self) -> int:
        """Load all .pkl files found in model_dir. Returns count loaded."""
        if not self.model_dir.exists():
            log.warning("model_dir_missing", path=str(self.model_dir))
            return 0
        count = 0
        for pkl_file in self.model_dir.glob("*.pkl"):
            key = pkl_file.stem  # e.g. "Kisenyi_Kampala__maize_grain"
            try:
                with open(pkl_file, "rb") as f:
                    self._models[key] = pickle.load(f)
                count += 1
                log.info("model_loaded", key=key)
            except Exception as exc:
                log.error("model_load_failed", key=key, error=str(exc))
        return count

    def get(self, market: str, crop: str) -> Optional[object]:
        return self._models.get(f"{market}__{crop}")

    @property
    def loaded_count(self) -> int:
        return len(self._models)

    def predict(self, market: str, crop: str, weeks_ahead: int) -> list[dict]:
        """Run Prophet inference or fall back to seasonal synthetic forecast."""
        model = self.get(market, crop)
        base_price = BASE_PRICES_UGX.get(crop, {}).get(market, 1000)

        if model is not None:
            return self._prophet_predict(model, weeks_ahead)
        log.warning("model_not_found_using_fallback", market=market, crop=crop)
        return self._fallback_predict(base_price, weeks_ahead)

    def _prophet_predict(self, model, weeks_ahead: int) -> list[dict]:
        future = model.make_future_dataframe(periods=weeks_ahead, freq="W")
        forecast = model.predict(future)
        last_rows = forecast.tail(weeks_ahead)
        results = []
        for _, row in last_rows.iterrows():
            yhat = max(int(row["yhat"]), 1)
            lower = max(int(row["yhat_lower"]), 1)
            upper = max(int(row["yhat_upper"]), 1)
            # Ensure bounds make sense
            if upper < yhat:
                upper = int(yhat * 1.10)
            if lower > yhat:
                lower = int(yhat * 0.90)
            results.append({
                "date": row["ds"].strftime("%Y-%m-%d"),
                "predicted_price_ugx": yhat,
                "lower_bound": lower,
                "upper_bound": upper,
            })
        return results

    def _fallback_predict(self, base_price: float, weeks_ahead: int) -> list[dict]:
        """Seasonal synthetic fallback when no model file is present.

        Uganda bimodal rainfall: Season 1 harvest Jun–Jul, Season 2 harvest Nov–Dec.
        Lean dry season Jan–Feb drives prices up.
        """
        today = datetime.utcnow()
        results = []
        rng = np.random.default_rng(seed=42)
        for i in range(1, weeks_ahead + 1):
            target_date = today + timedelta(weeks=i)
            month = target_date.month
            if month in (6, 7, 11, 12):
                seasonal_factor = 0.92  # post-harvest abundance
            elif month in (1, 2):
                seasonal_factor = 1.10  # lean dry season
            else:
                seasonal_factor = 1.0
            noise = rng.normal(0, base_price * 0.03)
            yhat = int(base_price * seasonal_factor + noise)
            results.append({
                "date": target_date.strftime("%Y-%m-%d"),
                "predicted_price_ugx": max(yhat, 1),
                "lower_bound": max(int(yhat * 0.90), 1),
                "upper_bound": max(int(yhat * 1.10), 1),
            })
        return results


async def train_all_models_from_feature_store() -> None:
    """
    Train one Prophet model per market–crop pair using soko_ml_db as the data source.
    Falls back to synthetic seed data for pairs with insufficient real observations.
    Replaces the CSV-based train_all_models() — call this from the retrain handler.
    """
    try:
        from prophet import Prophet
    except ImportError:
        log.error("prophet_not_installed")
        return

    from .feature_store_client import fetch_training_data

    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    model_dir.mkdir(parents=True, exist_ok=True)

    for market in SUPPORTED_MARKETS:
        for crop in SUPPORTED_CROPS:
            key      = f"{market}__{crop}"
            pkl_path = model_dir / f"{key}.pkl"

            df, source = await fetch_training_data(market, crop)

            if source == "farmgain_seed" or df.empty:
                df = _generate_pair_data(market, crop).rename(
                    columns={"date": "ds", "price_ugx": "y"}
                )[["ds", "y"]]

            df["ds"] = pd.to_datetime(df["ds"])
            df = df.dropna()
            if len(df) < 10:
                log.warning("insufficient_data_skip", key=key, rows=len(df))
                continue

            m = Prophet(
                yearly_seasonality=True,
                weekly_seasonality=False,
                daily_seasonality=False,
                seasonality_mode="multiplicative",
                interval_width=0.80,
            )
            m.add_seasonality(name="uganda_bimodal", period=26, fourier_order=5)
            m.fit(df)

            with open(pkl_path, "wb") as f:
                pickle.dump(m, f)
            log.info("model_trained", key=key, rows=len(df), source=source, path=str(pkl_path))


def train_all_models() -> None:
    """
    Legacy CSV-based trainer — retained for cold-start bootstrap only.
    In normal operation, use train_all_models_from_feature_store().
    Reads crop_prices_raw.csv if available; otherwise generates inline data.
    """
    try:
        from prophet import Prophet
    except ImportError:
        log.error("prophet_not_installed")
        return

    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    model_dir.mkdir(parents=True, exist_ok=True)

    data_dir  = Path(os.getenv("DATA_DIR", "../recommendation-service/data/raw"))
    data_path = data_dir / "crop_prices_raw.csv"
    df_all    = pd.read_csv(data_path) if data_path.exists() else _generate_training_data()

    for market in SUPPORTED_MARKETS:
        for crop in SUPPORTED_CROPS:
            key      = f"{market}__{crop}"
            pkl_path = model_dir / f"{key}.pkl"
            if pkl_path.exists():
                log.info("model_exists_skipping", key=key)
                continue

            subset = df_all[(df_all["market"] == market) & (df_all["crop"] == crop)].copy()
            if subset.empty or len(subset) < 20:
                subset = _generate_pair_data(market, crop)

            df = subset.rename(columns={"date": "ds", "price_ugx": "y"})[["ds", "y"]].copy()
            df["ds"] = pd.to_datetime(df["ds"])
            df = df.dropna()

            m = Prophet(
                yearly_seasonality=True,
                weekly_seasonality=False,
                daily_seasonality=False,
                seasonality_mode="multiplicative",
                interval_width=0.80,
            )
            m.add_seasonality(name="uganda_bimodal", period=26, fourier_order=5)
            m.fit(df)

            with open(pkl_path, "wb") as f:
                pickle.dump(m, f)
            log.info("model_trained", key=key, rows=len(df), path=str(pkl_path))


def _generate_training_data() -> pd.DataFrame:
    rows = []
    for market in SUPPORTED_MARKETS:
        for crop in SUPPORTED_CROPS:
            rows.extend(_generate_pair_data(market, crop).to_dict("records"))
    return pd.DataFrame(rows)


def _generate_pair_data(market: str, crop: str) -> pd.DataFrame:
    base = BASE_PRICES_UGX.get(crop, {}).get(market, 1000)
    dates = pd.date_range("2021-01-01", "2024-12-31", freq="W")
    rng = np.random.default_rng(seed=abs(hash(f"{market}{crop}")) % (2**31))
    prices = []
    for d in dates:
        month = d.month
        if month in (6, 7, 11, 12):
            sf = 0.92
        elif month in (1, 2):
            sf = 1.10
        else:
            sf = 1.0
        noise = rng.normal(0, base * 0.04)
        prices.append(max(int(base * sf + noise), 1))
    return pd.DataFrame({"date": dates, "price_ugx": prices, "market": market, "crop": crop})
