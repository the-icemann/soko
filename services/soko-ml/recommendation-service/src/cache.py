import json
import os
from typing import Optional

import redis.asyncio as aioredis
import structlog

log = structlog.get_logger()

REC_CACHE_TTL = int(os.getenv("REC_CACHE_TTL_SECONDS", "3600"))


def _farmers_key(buyer_id: str, top_n: int) -> str:
    return f"rec:farmers:{buyer_id}:{top_n}"


def _buyers_key(farmer_id: str, top_n: int) -> str:
    return f"rec:buyers:{farmer_id}:{top_n}"


async def get_redis_client() -> aioredis.Redis:
    host = os.getenv("REDIS_HOST", "redis")
    port = int(os.getenv("REDIS_PORT", "6379"))
    db = int(os.getenv("REDIS_DB", "0"))
    password = os.getenv("REDIS_PASSWORD") or None
    return aioredis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)


async def get_cached_farmers(
    client: aioredis.Redis, buyer_id: str, top_n: int
) -> Optional[list]:
    key = _farmers_key(buyer_id, top_n)
    try:
        data = await client.get(key)
        if data:
            log.info("cache_hit", key=key)
            return json.loads(data)
    except Exception as exc:
        log.warning("cache_get_error", key=key, error=str(exc))
    return None


async def set_cached_farmers(
    client: aioredis.Redis, buyer_id: str, top_n: int, result: list
) -> None:
    key = _farmers_key(buyer_id, top_n)
    try:
        await client.setex(key, REC_CACHE_TTL, json.dumps(result))
    except Exception as exc:
        log.warning("cache_set_error", key=key, error=str(exc))


async def get_cached_buyers(
    client: aioredis.Redis, farmer_id: str, top_n: int
) -> Optional[list]:
    key = _buyers_key(farmer_id, top_n)
    try:
        data = await client.get(key)
        if data:
            log.info("cache_hit", key=key)
            return json.loads(data)
    except Exception as exc:
        log.warning("cache_get_error", key=key, error=str(exc))
    return None


async def set_cached_buyers(
    client: aioredis.Redis, farmer_id: str, top_n: int, result: list
) -> None:
    key = _buyers_key(farmer_id, top_n)
    try:
        await client.setex(key, REC_CACHE_TTL, json.dumps(result))
    except Exception as exc:
        log.warning("cache_set_error", key=key, error=str(exc))


async def invalidate_farmer_recs(client: aioredis.Redis, buyer_id: str) -> None:
    """Invalidate all top_n variants for a buyer's farmer recommendations."""
    pattern = f"rec:farmers:{buyer_id}:*"
    try:
        async for key in client.scan_iter(pattern):
            await client.delete(key)
        log.info("cache_invalidated", pattern=pattern)
    except Exception as exc:
        log.warning("cache_invalidate_error", pattern=pattern, error=str(exc))


async def invalidate_buyer_recs(client: aioredis.Redis, farmer_id: str) -> None:
    """Invalidate all top_n variants for a farmer's buyer recommendations."""
    pattern = f"rec:buyers:{farmer_id}:*"
    try:
        async for key in client.scan_iter(pattern):
            await client.delete(key)
        log.info("cache_invalidated", pattern=pattern)
    except Exception as exc:
        log.warning("cache_invalidate_error", pattern=pattern, error=str(exc))
