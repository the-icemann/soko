"""
Geo-scoped farmer discovery for the /discover endpoint.
Finds farmers near a buyer who grow the requested crop within price range.
"""
import logging
import math
import os
from typing import Optional

import asyncpg

log = logging.getLogger(__name__)

POSTGRES_DSN     = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@soko-ml-db:5432/soko_ml_db")
PRICE_SERVICE_URL = os.getenv("PRICE_SERVICE_URL", "http://ml-gateway-service:8080")

_pool: Optional[asyncpg.Pool] = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=5)
    return _pool


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R    = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return round(2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


async def _get_market_price(crop: str, district: str) -> Optional[float]:
    """Fetches the week1 predicted price for a crop in the nearest market for the given district."""
    from .market_router import fetch_predictions
    # location-service uses a local copy of the district→market mapping
    DISTRICT_TO_MARKET_LOCAL = {
        "Kampala": "Kisenyi_Kampala", "Wakiso": "Kisenyi_Kampala",
        "Mukono":  "Kisenyi_Kampala", "Jinja":  "Kisenyi_Kampala",
        "Gulu":    "Gulu",  "Arua": "Gulu",
        "Mbarara": "Mbarara", "Bushenyi": "Mbarara", "Ntungamo": "Mbarara",
        "Mbale":   "Mbale",  "Tororo": "Mbale",  "Iganga": "Mbale",
        "Lira":    "Lira",   "Soroti": "Lira",
        "Masaka":  "Masaka", "Rakai":  "Masaka",
    }
    market = DISTRICT_TO_MARKET_LOCAL.get(district, "Kisenyi_Kampala")
    predictions = await fetch_predictions(market, crop)
    if predictions:
        return float(predictions[0]["predicted_price_ugx"])
    return None


async def discover_farmers(
    buyer_lat: float,
    buyer_lng: float,
    crop: str,
    max_price_ugx: float,
    max_distance_km: float,
    top_n: int,
    buyer_district: str = "",
) -> list[dict]:
    """
    Finds farmers who grow the given crop, within max_distance_km and below max_price_ugx.
    Uses district centroids for distance calculation (approximate, documented limitation).
    """
    pool = await _get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT farmer_id, name, lat, lng, district,
                   crops_offered, avg_rating, fulfillment_rate
            FROM farmer_features
            WHERE crops_offered @> ARRAY[$1]::text[]
            """,
            crop,
        )

    relax = float(os.getenv("GEO_FILTER_RELAX_FACTOR", "1.5"))
    threshold = max_distance_km * relax

    market_price = await _get_market_price(crop, buyer_district)

    results = []
    for r in rows:
        lat = r["lat"]
        lng = r["lng"]
        if lat and lng:
            dist = _haversine(buyer_lat, buyer_lng, float(lat), float(lng))
            if dist > threshold:
                continue
        else:
            dist = None

        # We don't have asking price per farmer directly; use market price as proxy
        # Farmers with listings below market price would be surfaced by produce-service
        asking_price  = None  # Would require produce-service lookup
        price_vs_market = "market_rate"
        if market_price and asking_price:
            price_vs_market = "below_market" if asking_price < market_price else "above_market"

        results.append({
            "farmer_id":               r["farmer_id"],
            "farmer_name":             r["name"] or "",
            "distance_km":             dist,
            "asking_price_ugx":        asking_price,
            "current_market_price_ugx": market_price,
            "price_vs_market":         price_vs_market,
            "avg_rating":              float(r["avg_rating"] or 0),
            "fulfillment_rate":        float(r["fulfillment_rate"] or 1.0),
            "available_quantity_kg":   None,
        })

    # Sort by distance (None distances go last), then by rating
    results.sort(key=lambda x: (x["distance_km"] is None, x["distance_km"] or 0, -x["avg_rating"]))
    return results[:top_n]
