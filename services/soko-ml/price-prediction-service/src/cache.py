import json
import os
from typing import Optional

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger()

PRICE_CACHE_TTL = int(os.getenv("PRICE_CACHE_TTL_SECONDS", "86400"))


def _price_key(market: str, crop: str, weeks_ahead: int) -> str:
    return f"price:v1:{market}:{crop}:{weeks_ahead}"


def _model_meta_key(market: str, crop: str) -> str:
    return f"model:meta:{market}:{crop}"


async def get_redis_client() -> aioredis.Redis:
    host = os.getenv("REDIS_HOST", "redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    password = os.getenv("REDIS_PASSWORD") or None
    return aioredis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)


async def get_cached_prediction(
    client: aioredis.Redis, market: str, crop: str, weeks_ahead: int
) -> Optional[dict]:
    key = _price_key(market, crop, weeks_ahead)
    try:
        data = await client.get(key)
        if data:
            log.info("cache_hit", key=key)
            return json.loads(data)
    except Exception as exc:
        log.warning("cache_get_error", key=key, error=str(exc))
    return None


async def set_cached_prediction(
    client: aioredis.Redis, market: str, crop: str, weeks_ahead: int, result: dict
) -> None:
    key = _price_key(market, crop, weeks_ahead)
    try:
        await client.setex(key, PRICE_CACHE_TTL, json.dumps(result))
        log.info("cache_set", key=key, ttl=PRICE_CACHE_TTL)
    except Exception as exc:
        log.warning("cache_set_error", key=key, error=str(exc))


async def invalidate_price_cache(
    client: aioredis.Redis, market: str, crop: str
) -> None:
    """Remove all cached predictions for a market-crop pair (called on model_deployed event)."""
    pattern = f"price:v1:{market}:{crop}:*"
    try:
        async for key in client.scan_iter(pattern):
            await client.delete(key)
        log.info("cache_invalidated", pattern=pattern)
    except Exception as exc:
        log.warning("cache_invalidate_error", pattern=pattern, error=str(exc))


async def set_model_meta(
    client: aioredis.Redis, market: str, crop: str, meta: dict
) -> None:
    key = _model_meta_key(market, crop)
    try:
        await client.setex(key, 7 * 86400, json.dumps(meta))
    except Exception as exc:
        log.warning("model_meta_set_error", key=key, error=str(exc))
