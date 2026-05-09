#!/usr/bin/env python3
"""
Generate synthetic farmer (200) and buyer (300) profiles for Uganda.
CSV columns mirror UserProfile / FarmerStats / BuyerStats in the user service DB model
so that real users can be swapped in without field-name transformation.

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

DISTRICTS = ["Kampala", "Gulu", "Mbarara", "Mbale", "Lira", "Masaka"]
CROPS = [
    "maize_grain", "yellow_beans", "irish_potatoes", "tomatoes",
    "matoke", "cassava_chips", "sorghum", "millet",
]

N_FARMERS = 200
N_BUYERS = 300

RNG = np.random.default_rng(seed=42)
random.seed(42)

RESPONSE_TIMES = ["< 1 hour", "< 4 hours", "< 24 hours", "1-2 days"]


def sample_list(items: list, min_n: int = 1, max_n: int = 3) -> list:
    # max 3 matches the user service specialties/interests constraint
    n = random.randint(min_n, min(max_n, len(items)))
    return random.sample(items, n)


def generate_farmers() -> pd.DataFrame:
    rows = []
    for i in range(1, N_FARMERS + 1):
        crops = sample_list(CROPS, 1, 3)
        district = random.choice(DISTRICTS)
        total_sales = int(RNG.integers(5, 200))
        total_listings = total_sales + int(RNG.integers(0, 20))
        total_reviews = int(RNG.integers(0, min(total_sales, 50)))
        rows.append({
            # ── UserProfile fields ────────────────────────────────────────────
            "id":           f"F{i:04d}",
            "full_name":    f"Farmer {i}",
            "role":         "farmer",
            "district":     district,
            "village":      f"Village {(i % 50) + 1}",
            "farm_name":    f"Farm {i}",
            "farmer_bio":   f"Growing {', '.join(crops)} in {district}.",
            "specialties":  ",".join(crops),      # comma-sep ≤3, mirrors DB column
            "verified":     random.choice([True, False]),
            # ── FarmerStats fields ────────────────────────────────────────────
            "average_rating":   round(float(RNG.uniform(2.5, 5.0)), 2),
            "total_sales":      total_sales,
            "total_earned":     int(RNG.uniform(500_000, 50_000_000)),
            "total_listings":   total_listings,
            "total_reviews":    total_reviews,
            "response_time":    random.choice(RESPONSE_TIMES),
            # ── ML-derived scoring signal (not in user service) ───────────────
            "fulfillment_rate": round(float(RNG.uniform(0.55, 1.0)), 3),
        })
    return pd.DataFrame(rows)


def generate_buyers() -> pd.DataFrame:
    rows = []
    for i in range(1, N_BUYERS + 1):
        crops = sample_list(CROPS, 1, 3)
        district = random.choice(DISTRICTS)
        total_orders = int(RNG.integers(1, 80))
        rows.append({
            # ── UserProfile fields ────────────────────────────────────────────
            "id":           f"B{i:04d}",
            "full_name":    f"Buyer {i}",
            "role":         "buyer",
            "district":     district,
            "interests":    ",".join(crops),       # comma-sep ≤3, mirrors DB column
            "verified":     random.choice([True, False]),
            # ── BuyerStats fields ─────────────────────────────────────────────
            "total_orders":   total_orders,
            "total_spent":    int(RNG.uniform(50_000, 5_000_000)),
            "wishlist_count": int(RNG.integers(0, 20)),
            # ── ML-derived scoring signal (not in user service) ───────────────
            "payment_reliability": round(float(RNG.uniform(0.40, 1.0)), 3),
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
    print(f"Districts: {DISTRICTS}")


if __name__ == "__main__":
    main()
