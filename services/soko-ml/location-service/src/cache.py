"""
Redis cache layer for the location-service.
All keys and TTLs follow the registry defined in the system spec.
"""
import json
import logging
import os
from decimal import Decimal
from typing import Optional

import redis.asyncio as aioredis


class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)

log = logging.getLogger(__name__)

DIST_TTL         = int(os.getenv("MAPS_DISTANCE_CACHE_TTL_SECONDS", "2592000"))  # 30 days
ROUTE_TTL        = 6 * 3600          # 6 hours
DISCOVER_TTL     = 3600              # 1 hour
MARKET_REG_TTL   = 6 * 3600         # 6 hours

_redis: Optional[aioredis.Redis] = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        host     = os.getenv("REDIS_HOST", "redis")
        port     = int(os.getenv("REDIS_PORT", "6379"))
        db       = int(os.getenv("REDIS_DB", "0"))
        password = os.getenv("REDIS_PASSWORD") or None
        _redis   = aioredis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


# ── Distance cache ────────────────────────────────────────────────────────────

async def get_distance(farmer_id: str, market_id: str) -> Optional[float]:
    redis = await get_redis()
    try:
        val = await redis.get(f"dist:{farmer_id}:{market_id}")
        return float(val) if val is not None else None
    except Exception as exc:
        log.warning(f"cache get_distance error: {exc}")
        return None


async def set_distance(farmer_id: str, market_id: str, km: float) -> None:
    redis = await get_redis()
    try:
        await redis.setex(f"dist:{farmer_id}:{market_id}", DIST_TTL, str(km))
    except Exception as exc:
        log.warning(f"cache set_distance error: {exc}")


async def invalidate_farmer_distances(farmer_id: str) -> None:
    redis = await get_redis()
    try:
        async for key in redis.scan_iter(f"dist:{farmer_id}:*"):
            await redis.delete(key)
        async for key in redis.scan_iter(f"route:{farmer_id}:*"):
            await redis.delete(key)
    except Exception as exc:
        log.warning(f"cache invalidate_farmer_distances error: {exc}")


# ── Route cache ───────────────────────────────────────────────────────────────

async def get_route(farmer_id: str, crop: str, quantity: float) -> Optional[dict]:
    redis = await get_redis()
    key   = f"route:{farmer_id}:{crop}:{int(quantity)}"
    try:
        val = await redis.get(key)
        return json.loads(val) if val else None
    except Exception as exc:
        log.warning(f"cache get_route error: {exc}")
        return None


async def set_route(farmer_id: str, crop: str, quantity: float, result: dict) -> None:
    redis = await get_redis()
    key   = f"route:{farmer_id}:{crop}:{int(quantity)}"
    try:
        await redis.setex(key, ROUTE_TTL, json.dumps(result, cls=_Encoder))
    except Exception as exc:
        log.warning(f"cache set_route error: {exc}")


# ── Discover cache ────────────────────────────────────────────────────────────

async def get_discover(buyer_id: str, crop: str, max_price: float) -> Optional[dict]:
    redis = await get_redis()
    key   = f"discover:{buyer_id}:{crop}:{int(max_price)}"
    try:
        val = await redis.get(key)
        return json.loads(val) if val else None
    except Exception as exc:
        log.warning(f"cache get_discover error: {exc}")
        return None


async def set_discover(buyer_id: str, crop: str, max_price: float, result: dict) -> None:
    redis = await get_redis()
    key   = f"discover:{buyer_id}:{crop}:{int(max_price)}"
    try:
        await redis.setex(key, DISCOVER_TTL, json.dumps(result, cls=_Encoder))
    except Exception as exc:
        log.warning(f"cache set_discover error: {exc}")


# ── Market registry cache ─────────────────────────────────────────────────────

async def get_market_registry() -> Optional[list]:
    redis = await get_redis()
    try:
        val = await redis.get("market:registry")
        return json.loads(val) if val else None
    except Exception as exc:
        log.warning(f"cache get_market_registry error: {exc}")
        return None


async def set_market_registry(markets: list) -> None:
    redis = await get_redis()
    try:
        await redis.setex("market:registry", MARKET_REG_TTL, json.dumps(markets, cls=_Encoder))
    except Exception as exc:
        log.warning(f"cache set_market_registry error: {exc}")
