"""
Health checks for all external dependencies of the data-ingestion-service.
"""
import logging
import os

import asyncpg
import httpx

log = logging.getLogger(__name__)

USER_SERVICE_URL    = os.getenv("USER_SERVICE_URL",    "http://user-service:3003")
ORDER_SERVICE_URL   = os.getenv("ORDER_SERVICE_URL",   "http://order-service:3002")
LISTING_SERVICE_URL = os.getenv("LISTING_SERVICE_URL", "http://produce-service:3004")
POSTGRES_DSN        = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@soko-ml-db:5432/soko_ml_db")


async def check_postgres() -> str:
    try:
        conn = await asyncpg.connect(POSTGRES_DSN)
        await conn.fetchval("SELECT 1")
        await conn.close()
        return "ok"
    except Exception as exc:
        log.warning(f"Postgres health check failed: {exc}")
        return "unreachable"


async def check_service(url: str, name: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{url}/health")
            return "ok" if resp.status_code == 200 else "degraded"
    except Exception:
        return "unreachable"


async def full_health_check() -> dict:
    postgres = await check_postgres()
    user     = await check_service(USER_SERVICE_URL,    "user-service")
    order    = await check_service(ORDER_SERVICE_URL,   "order-service")
    listing  = await check_service(LISTING_SERVICE_URL, "produce-service")

    overall = "ok" if all(s == "ok" for s in [postgres, user, order, listing]) else "degraded"
    return {
        "status":   overall,
        "postgres": postgres,
        "user-service":    user,
        "order-service":   order,
        "produce-service": listing,
    }
