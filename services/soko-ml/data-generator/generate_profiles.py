#!/usr/bin/env python3
"""
Generate synthetic farmer (200) and buyer (300) profiles for Uganda.
All profiles use crops and markets from the Soko ML service spec.

Output:
  recommendation-service/data/raw/farmers.csv
  recommendation-service/data/raw/buyers.csv
"""
import os
import random
from pathlib import Path

import numpy as np
import pandas as pd

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "recommendation-service/data/raw"))

MARKETS = ["Kisenyi_Kampala", "Gulu", "Mbarara", "Mbale", "Lira", "Masaka"]
CROPS = [
    "maize_grain", "yellow_beans", "irish_potatoes", "tomatoes",
    "matoke", "cassava_chips", "sorghum", "millet",
]

N_FARMERS = 200
N_BUYERS = 300

RNG = np.random.default_rng(seed=42)
random.seed(42)


def sample_list(items: list, min_n: int = 1, max_n: int = 4) -> list:
    n = random.randint(min_n, min(max_n, len(items)))
    return random.sample(items, n)


def generate_farmers() -> pd.DataFrame:
    rows = []
    for i in range(1, N_FARMERS + 1):
        farmer_id = f"F{i:04d}"
        crops = sample_list(CROPS, 1, 4)
        markets = sample_list(MARKETS, 1, 3)
        avg_rating = round(float(RNG.uniform(2.5, 5.0)), 2)
        fulfillment_rate = round(float(RNG.uniform(0.55, 1.0)), 3)
        years_active = int(RNG.integers(1, 12))
        total_sales_ugx = int(RNG.uniform(500_000, 50_000_000))
        rows.append({
            "farmer_id": farmer_id,
            "name": f"Farmer_{i}",
            "crops_offered": ",".join(crops),
            "markets_served": ",".join(markets),
            "avg_rating": avg_rating,
            "fulfillment_rate": fulfillment_rate,
            "years_active": years_active,
            "total_sales_ugx": total_sales_ugx,
        })
    return pd.DataFrame(rows)


def generate_buyers() -> pd.DataFrame:
    rows = []
    for i in range(1, N_BUYERS + 1):
        buyer_id = f"B{i:04d}"
        preferred_crops = sample_list(CROPS, 1, 5)
        preferred_markets = sample_list(MARKETS, 1, 3)
        payment_reliability = round(float(RNG.uniform(0.40, 1.0)), 3)
        avg_order_volume_kg = round(float(RNG.uniform(50, 2000)), 1)
        order_frequency_per_month = round(float(RNG.uniform(0.5, 8.0)), 1)
        rows.append({
            "buyer_id": buyer_id,
            "name": f"Buyer_{i}",
            "preferred_crops": ",".join(preferred_crops),
            "preferred_markets": ",".join(preferred_markets),
            "payment_reliability": payment_reliability,
            "avg_order_volume_kg": avg_order_volume_kg,
            "order_frequency_per_month": order_frequency_per_month,
        })
    return pd.DataFrame(rows)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    farmers = generate_farmers()
    buyers = generate_buyers()

    farmers_path = OUTPUT_DIR / "farmers.csv"
    buyers_path = OUTPUT_DIR / "buyers.csv"

    farmers.to_csv(farmers_path, index=False)
    buyers.to_csv(buyers_path, index=False)

    print(f"Generated {len(farmers)} farmers → {farmers_path}")
    print(f"Generated {len(buyers)} buyers  → {buyers_path}")
    print(f"Crops: {CROPS}")
    print(f"Markets: {MARKETS}")


if __name__ == "__main__":
    main()
