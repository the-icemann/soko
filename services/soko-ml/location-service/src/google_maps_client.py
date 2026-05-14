"""
Google Maps Distance Matrix API client.
Batches all market distance requests in a single API call per farmer query.
Results are cached in Redis for DIST_TTL (30 days) to minimise API spend.
"""
import logging
import os
from typing import Optional

import httpx

log = logging.getLogger(__name__)

MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "")
MATRIX_URL   = "https://maps.googleapis.com/maps/api/distancematrix/json"


async def get_road_distances(
    origin_lat: float,
    origin_lng: float,
    destinations: list[dict],  # list of {"market_id": str, "lat": float, "lng": float}
) -> dict[str, float]:
    """
    Calls the Distance Matrix API once with all destinations in a single request.
    Returns {market_id: distance_km} for each destination.
    Falls back to Haversine straight-line distance if API key is missing or call fails.
    """
    if not MAPS_API_KEY:
        log.warning("GOOGLE_MAPS_API_KEY not set — using Haversine straight-line distances")
        return _haversine_fallback(origin_lat, origin_lng, destinations)

    origin_str = f"{origin_lat},{origin_lng}"
    dest_str   = "|".join(f"{d['lat']},{d['lng']}" for d in destinations)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                MATRIX_URL,
                params={
                    "origins":      origin_str,
                    "destinations": dest_str,
                    "mode":         "driving",
                    "key":          MAPS_API_KEY,
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning(f"Google Maps API call failed: {exc} — falling back to Haversine")
        return _haversine_fallback(origin_lat, origin_lng, destinations)

    if data.get("status") != "OK":
        log.warning(f"Maps API status={data.get('status')} — falling back to Haversine")
        return _haversine_fallback(origin_lat, origin_lng, destinations)

    elements = data.get("rows", [{}])[0].get("elements", [])
    result: dict[str, float] = {}

    for dest, element in zip(destinations, elements):
        market_id = dest["market_id"]
        if element.get("status") == "OK":
            result[market_id] = round(element["distance"]["value"] / 1000.0, 1)
        else:
            # Fallback for this specific destination
            result[market_id] = _haversine_single(origin_lat, origin_lng, dest["lat"], dest["lng"])

    return result


def _haversine_single(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    import math
    R     = 6371.0
    phi1  = math.radians(lat1)
    phi2  = math.radians(lat2)
    dphi  = math.radians(lat2 - lat1)
    dlam  = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return round(2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 1)


def _haversine_fallback(
    origin_lat: float,
    origin_lng: float,
    destinations: list[dict],
) -> dict[str, float]:
    return {
        d["market_id"]: _haversine_single(origin_lat, origin_lng, d["lat"], d["lng"])
        for d in destinations
    }
