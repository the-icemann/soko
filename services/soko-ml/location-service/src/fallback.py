"""
Three-tier fallback system for crops/markets not yet in the ML model.
Checks the coverage_map table in soko_ml_db — no hardcoded lists.
Never raises. Always returns a response.
"""
import json
import logging
import os
from typing import Optional

import asyncpg

from .transport_cost import TRANSPORT_DISCLAIMER

log = logging.getLogger(__name__)

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@soko-ml-db:5432/soko_ml_db")

CATEGORY_PRICE_BANDS: dict[str, dict] = {
    "cereals":    {"low": 800,   "high": 2000,  "crops": {"maize_grain", "sorghum", "millet", "rice"}},
    "legumes":    {"low": 2000,  "high": 5000,  "crops": {"yellow_beans", "groundnuts", "soybeans"}},
    "vegetables": {"low": 400,   "high": 1500,  "crops": {"tomatoes", "kale", "nakati", "cabbage", "onions", "eggplant"}},
    "tubers":     {"low": 300,   "high": 1200,  "crops": {"irish_potatoes", "cassava_chips", "sweet_potatoes", "yams"}},
    "cash_crops": {"low": 3000,  "high": 15000, "crops": {"coffee", "vanilla", "cotton"}},
    "fruits":     {"low": 400,   "high": 1200,  "crops": {"matoke"}},
}

KAFKA_ML_TOPIC = os.getenv("KAFKA_ML_EVENTS_TOPIC", "soko.ml.events")

_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def _find_category(crop: str) -> Optional[str]:
    for category, data in CATEGORY_PRICE_BANDS.items():
        if crop in data["crops"]:
            return category
    return None


async def determine_tier(crop: str, market: str) -> int:
    """
    Returns 1, 2, or 3 based on coverage_map state.
    1 = full ML coverage
    2 = partial or category coverage only
    3 = out of scope
    """
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT observation_count, is_model_ready FROM coverage_map WHERE crop = $1 AND market = $2",
                crop, market,
            )
    except Exception:
        return 2  # conservative default if DB unreachable

    if row is None:
        category = _find_category(crop)
        return 2 if category else 3

    if row["is_model_ready"]:
        return 1

    return 2


async def build_tier2_response(
    farmer_id: str,
    crop: str,
    quantity_kg: float,
    max_distance_km: float,
) -> dict:
    """Tier 2: returns category-level price band estimate."""
    category = _find_category(crop)
    band     = CATEGORY_PRICE_BANDS.get(category, {}) if category else {}

    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT observation_count, min_observations_needed
                FROM coverage_map WHERE crop = $1
                LIMIT 1
                """,
                crop,
            )
        obs_count = row["observation_count"] if row else 0
        obs_needed = row["min_observations_needed"] if row else 52
    except Exception:
        obs_count  = 0
        obs_needed = 52

    price_low  = band.get("low",  0)
    price_high = band.get("high", 0)

    return {
        "farmer_id":    farmer_id,
        "crop":         crop,
        "quantity_kg":  quantity_kg,
        "currency":     "UGX",
        "tier":         2,
        "ranked_markets": [],
        "transport_disclaimer": TRANSPORT_DISCLAIMER,
        "cached_distances": False,
        "tier_message": (
            f"Limited data available for {crop} — estimate only. "
            f"Category price range: {price_low:,}–{price_high:,} UGX/kg. "
            f"{obs_count} of {obs_needed} observations collected so far."
        ),
    }


def build_tier3_response(farmer_id: str, crop: str, quantity_kg: float, reason: str = "") -> dict:
    """Tier 3: crop not recognised. Returns graceful no-data response. Never raises."""
    return {
        "farmer_id":    farmer_id,
        "crop":         crop,
        "quantity_kg":  quantity_kg,
        "currency":     "UGX",
        "tier":         3,
        "ranked_markets": [],
        "transport_disclaimer": TRANSPORT_DISCLAIMER,
        "cached_distances": False,
        "tier_message": (
            f"We don't have market intelligence for '{crop}' yet. "
            "Our team has been notified and will add coverage soon. "
            "You can still list your produce and transact normally on Soko."
        ),
    }


async def handle_unknown_crop(crop: str, farmer_id: str) -> None:
    """
    Records the gap in coverage_gaps table and publishes Kafka events.
    Called when Tier 3 triggers. Never raises.
    """
    try:
        from .gap_notifier import record_and_notify_gap
        await record_and_notify_gap(crop, farmer_id)
    except Exception as exc:
        log.warning(f"Gap notification failed for crop={crop}: {exc}")
