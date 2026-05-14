"""
Core routing logic: given a farmer location + crop + quantity,
ranks markets by net value after transport cost.
"""
import logging
import os
from typing import Optional

import asyncpg

from .cache import (
    get_distance, set_distance,
    get_market_registry, set_market_registry,
)
from .google_maps_client import get_road_distances
from .transport_cost import estimate as estimate_transport
from .sell_signal import derive_signal

log = logging.getLogger(__name__)

POSTGRES_DSN     = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@soko-ml-db:5432/soko_ml_db")
PRICE_SERVICE_URL = os.getenv("PRICE_SERVICE_URL", "http://ml-gateway-service:8080")
DEFAULT_MAX_KM   = float(os.getenv("DEFAULT_MAX_DISTANCE_KM", "150"))

_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ── Market registry ───────────────────────────────────────────────────────────

async def load_market_registry() -> list[dict]:
    """Loads markets from soko_ml_db. Falls back to Redis cache if DB is unreachable."""
    try:
        pool = await _get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT market_id, name, lat, lng, district FROM market_registry WHERE active = TRUE"
            )
        markets = [dict(r) for r in rows]
        await set_market_registry(markets)
        return markets
    except Exception as exc:
        log.warning(f"Failed to load market registry from DB: {exc} — trying Redis cache")
        cached = await get_market_registry()
        if cached:
            return cached
        log.error("Market registry unavailable from both DB and cache")
        return []


# ── Distance fetching with cache ──────────────────────────────────────────────

async def get_distances_for_farmer(
    farmer_id: str,
    farmer_lat: float,
    farmer_lng: float,
    markets: list[dict],
) -> tuple[dict[str, float], bool]:
    """
    Returns ({market_id: km}, cached: bool).
    Hits Redis cache first; only calls Maps API for uncached markets.
    """
    result: dict[str, float] = {}
    uncached: list[dict]     = []

    for m in markets:
        mid = m["market_id"]
        cached_km = await get_distance(farmer_id, mid)
        if cached_km is not None:
            result[mid] = cached_km
        else:
            uncached.append(m)

    all_cached = len(uncached) == 0

    if uncached:
        fresh = await get_road_distances(
            farmer_lat, farmer_lng,
            [{"market_id": m["market_id"], "lat": float(m["lat"]), "lng": float(m["lng"])} for m in uncached],
        )
        for mid, km in fresh.items():
            result[mid] = km
            await set_distance(farmer_id, mid, km)

    return result, all_cached


# ── Price fetching from gateway ───────────────────────────────────────────────

async def fetch_predictions(market: str, crop: str, weeks: int = 4) -> list[dict]:
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{PRICE_SERVICE_URL}/price/predict",
                json={"market": market, "crop": crop, "weeks_ahead": weeks},
            )
            if resp.status_code == 200:
                return resp.json().get("predictions", [])
    except Exception as exc:
        log.warning(f"Failed to fetch predictions for {market}/{crop}: {exc}")
    return []


# ── Main routing function ─────────────────────────────────────────────────────

async def route(
    farmer_id: str,
    farmer_lat: float,
    farmer_lng: float,
    crop: str,
    quantity_kg: float,
    max_distance_km: float = DEFAULT_MAX_KM,
) -> dict:
    """
    Returns the full /route response dict including tier determination,
    ranked markets, signals, and disclaimer.
    """
    from .fallback import determine_tier, build_tier2_response, build_tier3_response

    markets = await load_market_registry()
    if not markets:
        return build_tier3_response(farmer_id, crop, quantity_kg, "Market registry unavailable")

    # Filter by distance
    all_distances, cached = await get_distances_for_farmer(farmer_id, farmer_lat, farmer_lng, markets)

    reachable = [
        m for m in markets
        if all_distances.get(m["market_id"], 9999) <= max_distance_km
    ]

    if not reachable:
        # Relax to nearest market regardless of distance
        nearest_id = min(all_distances, key=lambda k: all_distances[k])
        reachable  = [m for m in markets if m["market_id"] == nearest_id]

    from .transport_cost import TRANSPORT_DISCLAIMER

    ranked = []
    for market in reachable:
        mid         = market["market_id"]
        distance_km = all_distances.get(mid, 0)

        tier = await determine_tier(crop, mid)
        if tier == 3:
            continue  # Skip markets with no ML coverage — Tier 3 handled separately

        transport = estimate_transport(distance_km)
        transport_cost = transport["ugx_per_kg"]

        predictions = await fetch_predictions(mid, crop)
        if not predictions:
            continue

        week1_price = float(predictions[0]["predicted_price_ugx"])
        net_value   = week1_price - transport_cost
        total_net   = round(net_value * quantity_kg, 0)

        signal_data = derive_signal(crop, predictions)

        ranked.append({
            "market":                   mid,
            "distance_km":              distance_km,
            "transport_mode":           transport["mode"],
            "transport_cost_per_kg_ugx": transport_cost,
            "predicted_price_ugx":      week1_price,
            "net_value_per_kg_ugx":     round(net_value, 0),
            "total_net_value_ugx":      total_net,
            "signal":                   signal_data["signal"],
            "signal_reason":            signal_data["reason"],
            "confidence":               signal_data["confidence"],
        })

    if not ranked:
        return await build_tier2_response(farmer_id, crop, quantity_kg, max_distance_km)

    ranked.sort(key=lambda x: x["net_value_per_kg_ugx"], reverse=True)

    return {
        "farmer_id":           farmer_id,
        "crop":                crop,
        "quantity_kg":         quantity_kg,
        "currency":            "UGX",
        "tier":                1,
        "ranked_markets":      ranked,
        "transport_disclaimer": TRANSPORT_DISCLAIMER,
        "cached_distances":    cached,
    }
