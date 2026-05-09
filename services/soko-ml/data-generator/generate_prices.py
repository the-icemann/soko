#!/usr/bin/env python3
"""
Generate synthetic weekly crop price data for 6 Ugandan markets × 8 crops, 2021–2024.
All prices in UGX. Seasonal patterns follow Uganda bimodal rainfall:
  - Season 1 harvest: Jun–Jul (prices dip)
  - Season 2 harvest: Nov–Dec (prices dip)
  - Lean dry season: Jan–Feb (prices peak)

Output: recommendation-service/data/raw/crop_prices_raw.csv
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "recommendation-service/data/raw"))
OUTPUT_FILE = OUTPUT_DIR / "crop_prices_raw.csv"

MARKETS = ["Kisenyi_Kampala", "Gulu", "Mbarara", "Mbale", "Lira", "Masaka"]
CROPS = [
    "maize_grain", "yellow_beans", "irish_potatoes", "tomatoes",
    "matoke", "cassava_chips", "sorghum", "millet",
]

# Base wholesale prices per kg in UGX (FarmGain Africa Uganda approximations)
BASE_PRICES: dict[str, dict[str, float]] = {
    "maize_grain":    {"Kisenyi_Kampala": 1300, "Gulu": 1100, "Mbarara": 1250, "Mbale": 1150, "Lira": 1050, "Masaka": 1200},
    "yellow_beans":   {"Kisenyi_Kampala": 3200, "Gulu": 2800, "Mbarara": 3000, "Mbale": 2900, "Lira": 2700, "Masaka": 3100},
    "irish_potatoes": {"Kisenyi_Kampala": 800,  "Gulu": 700,  "Mbarara": 850,  "Mbale": 750,  "Lira": 680,  "Masaka": 820},
    "tomatoes":       {"Kisenyi_Kampala": 1500, "Gulu": 1200, "Mbarara": 1400, "Mbale": 1300, "Lira": 1100, "Masaka": 1350},
    "matoke":         {"Kisenyi_Kampala": 600,  "Gulu": 500,  "Mbarara": 650,  "Mbale": 550,  "Lira": 480,  "Masaka": 620},
    "cassava_chips":  {"Kisenyi_Kampala": 950,  "Gulu": 850,  "Mbarara": 900,  "Mbale": 880,  "Lira": 820,  "Masaka": 920},
    "sorghum":        {"Kisenyi_Kampala": 1100, "Gulu": 950,  "Mbarara": 1050, "Mbale": 1000, "Lira": 920,  "Masaka": 1080},
    "millet":         {"Kisenyi_Kampala": 2200, "Gulu": 1900, "Mbarara": 2100, "Mbale": 2000, "Lira": 1850, "Masaka": 2150},
}

# Annual price drift rate (approximate Uganda food CPI ~7% p.a.)
ANNUAL_DRIFT = 0.07


def seasonal_factor(month: int) -> float:
    """Uganda bimodal seasonality multiplier."""
    if month in (6, 7):
        return 0.88   # Season 1 harvest — abundance
    if month in (11, 12):
        return 0.92   # Season 2 harvest — moderate dip
    if month in (1, 2):
        return 1.12   # Lean dry season — scarcity premium
    if month in (3, 4, 5):
        return 1.04   # Pre-season 1 — slight rise
    return 1.0        # Aug–Oct: moderate


def generate_pair(market: str, crop: str) -> pd.DataFrame:
    base = BASE_PRICES[crop][market]
    dates = pd.date_range("2021-01-01", "2024-12-31", freq="W")
    rng = np.random.default_rng(seed=abs(hash(f"{market}{crop}")) % (2**31))

    rows = []
    for d in dates:
        years_elapsed = (d.year - 2021) + (d.month - 1) / 12
        drift = (1 + ANNUAL_DRIFT) ** years_elapsed
        sf = seasonal_factor(d.month)
        noise = rng.normal(0, base * 0.04)
        price = max(int(base * drift * sf + noise), 1)
        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "market": market,
            "crop": crop,
            "price_ugx": price,
            "price_type": "wholesale",
        })
    return pd.DataFrame(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = []
    for market in MARKETS:
        for crop in CROPS:
            frames.append(generate_pair(market, crop))

    df = pd.concat(frames, ignore_index=True)
    df.to_csv(OUTPUT_FILE, index=False)
    print(f"Generated {len(df):,} rows → {OUTPUT_FILE}")
    print(f"Markets: {MARKETS}")
    print(f"Crops:   {CROPS}")
    print(f"Date range: {df['date'].min()} – {df['date'].max()}")


if __name__ == "__main__":
    main()
