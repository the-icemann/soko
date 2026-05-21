"""
Postgres read/write for all ML feature tables.
Uses asyncpg connection pooling. All queries are parameterised — no string formatting in SQL.
"""
import os
import logging
from typing import Optional

import asyncpg

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@soko-ml-db:5432/soko_ml_db")
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


# ── Bootstrap guards ──────────────────────────────────────────────────────────

async def is_bootstrap_needed() -> bool:
    """Returns True if all feature tables are empty (fresh environment)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        farmer_count = await conn.fetchval("SELECT COUNT(*) FROM farmer_features")
        buyer_count  = await conn.fetchval("SELECT COUNT(*) FROM buyer_features")
        price_count  = await conn.fetchval(
            "SELECT COUNT(*) FROM price_observations WHERE source = 'soko_order'"
        )
    return farmer_count == 0 and buyer_count == 0 and price_count == 0


# ── Farmer features ───────────────────────────────────────────────────────────

async def upsert_farmer(record: dict) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO farmer_features (
                farmer_id, name, lat, lng, district,
                crops_offered, markets_served,
                avg_rating, fulfillment_rate, avg_response_time_hrs,
                total_orders_completed, total_orders_cancelled,
                total_listings, last_active_at, synced_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7,
                $8, $9, $10,
                $11, $12,
                $13, $14, NOW()
            )
            ON CONFLICT (farmer_id) DO UPDATE SET
                name                  = EXCLUDED.name,
                lat                   = EXCLUDED.lat,
                lng                   = EXCLUDED.lng,
                district              = EXCLUDED.district,
                crops_offered         = EXCLUDED.crops_offered,
                markets_served        = EXCLUDED.markets_served,
                avg_rating            = EXCLUDED.avg_rating,
                fulfillment_rate      = EXCLUDED.fulfillment_rate,
                avg_response_time_hrs = EXCLUDED.avg_response_time_hrs,
                total_orders_completed  = EXCLUDED.total_orders_completed,
                total_orders_cancelled  = EXCLUDED.total_orders_cancelled,
                total_listings        = EXCLUDED.total_listings,
                last_active_at        = EXCLUDED.last_active_at,
                synced_at             = NOW()
            """,
            record["farmer_id"],
            record["name"],
            record.get("lat"),
            record.get("lng"),
            record.get("district"),
            record.get("crops_offered", []),
            record.get("markets_served", []),
            record.get("avg_rating", 0.0),
            record.get("fulfillment_rate", 1.0),
            record.get("avg_response_time_hrs", 24.0),
            record.get("total_orders_completed", 0),
            record.get("total_orders_cancelled", 0),
            record.get("total_listings", 0),
            record.get("last_active_at"),
        )


async def bulk_upsert_farmers(records: list[dict]) -> int:
    count = 0
    for record in records:
        try:
            await upsert_farmer(record)
            count += 1
        except Exception as exc:
            log.error(f"Failed to upsert farmer {record.get('farmer_id')}: {exc}")
    return count


# ── Buyer features ────────────────────────────────────────────────────────────

async def upsert_buyer(record: dict) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO buyer_features (
                buyer_id, name, lat, lng, district,
                preferred_crops, preferred_markets,
                avg_order_volume_kg, payment_reliability,
                avg_spend_per_order, total_purchases,
                last_active_at, synced_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7,
                $8, $9,
                $10, $11,
                $12, NOW()
            )
            ON CONFLICT (buyer_id) DO UPDATE SET
                name               = EXCLUDED.name,
                lat                = EXCLUDED.lat,
                lng                = EXCLUDED.lng,
                district           = EXCLUDED.district,
                preferred_crops    = EXCLUDED.preferred_crops,
                preferred_markets  = EXCLUDED.preferred_markets,
                avg_order_volume_kg = EXCLUDED.avg_order_volume_kg,
                payment_reliability = EXCLUDED.payment_reliability,
                avg_spend_per_order = EXCLUDED.avg_spend_per_order,
                total_purchases    = EXCLUDED.total_purchases,
                last_active_at     = EXCLUDED.last_active_at,
                synced_at          = NOW()
            """,
            record["buyer_id"],
            record["name"],
            record.get("lat"),
            record.get("lng"),
            record.get("district"),
            record.get("preferred_crops", []),
            record.get("preferred_markets", []),
            record.get("avg_order_volume_kg", 0.0),
            record.get("payment_reliability", 1.0),
            record.get("avg_spend_per_order", 0.0),
            record.get("total_purchases", 0),
            record.get("last_active_at"),
        )


async def bulk_upsert_buyers(records: list[dict]) -> int:
    count = 0
    for record in records:
        try:
            await upsert_buyer(record)
            count += 1
        except Exception as exc:
            log.error(f"Failed to upsert buyer {record.get('buyer_id')}: {exc}")
    return count


# ── Price observations ────────────────────────────────────────────────────────

async def get_rolling_mean_and_std(market: str, crop: str, last_n: int = 30) -> tuple[float, float]:
    """Returns (mean, std) of last_n price observations for outlier rejection."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT price_per_kg FROM price_observations
            WHERE market = $1 AND crop = $2
            ORDER BY observed_at DESC
            LIMIT $3
            """,
            market, crop, last_n,
        )
    if len(rows) < 2:
        return 0.0, 0.0
    prices = [float(r["price_per_kg"]) for r in rows]
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    return mean, variance ** 0.5


async def insert_price_observation(record: dict) -> bool:
    """
    Inserts a price observation. Returns False and logs a warning if the price
    is a statistical outlier (> 3 sigma from rolling mean of last 30 observations).
    Never raises.
    """
    market = record["market"]
    crop   = record["crop"]
    price  = float(record["price_per_kg"])

    sigma_threshold = float(os.getenv("PRICE_ANOMALY_SIGMA_THRESHOLD", "3.0"))
    mean, std = await get_rolling_mean_and_std(market, crop)
    if std > 0 and abs(price - mean) > sigma_threshold * std:
        log.warning(
            f"Outlier price rejected: {crop}@{market} price={price} "
            f"mean={mean:.0f} std={std:.0f} (>{sigma_threshold}σ)"
        )
        return False

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO price_observations
                (observed_at, market, crop, price_per_kg, currency, source, order_id, quantity_kg)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            record["observed_at"],
            market,
            crop,
            price,
            record.get("currency", "UGX"),
            record.get("source", "soko_order"),
            record.get("order_id"),
            record.get("quantity_kg"),
        )
    return True


async def bulk_insert_price_observations(records: list[dict]) -> int:
    count = 0
    for record in records:
        try:
            inserted = await insert_price_observation(record)
            if inserted:
                count += 1
        except Exception as exc:
            log.error(f"Failed to insert price observation: {exc}")
    return count


# ── Coverage map ──────────────────────────────────────────────────────────────

async def get_coverage_status(crop: str, market: str) -> Optional[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT crop, market, observation_count, min_observations_needed,
                   is_model_ready, last_retrain_at
            FROM coverage_map
            WHERE crop = $1 AND market = $2
            """,
            crop, market,
        )
    if row is None:
        return None
    return dict(row)


async def get_all_coverage() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT crop, market, observation_count, min_observations_needed, is_model_ready "
            "FROM coverage_map ORDER BY crop, market"
        )
    return [dict(r) for r in rows]


async def mark_retrained(crop: str, market: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE coverage_map SET last_retrain_at = NOW() WHERE crop = $1 AND market = $2",
            crop, market,
        )


# ── Coverage gaps ─────────────────────────────────────────────────────────────

PRIORITY_THRESHOLDS = {"low": (1, 5), "medium": (5, 15), "high": (15, 99999)}


def _compute_priority(frequency: int) -> str:
    for priority, (lo, hi) in PRIORITY_THRESHOLDS.items():
        if lo <= frequency < hi:
            return priority
    return "high"


async def record_coverage_gap(crop_submitted: str, category_guess: str, reported_by: str) -> dict:
    """Upsert a coverage gap record. Returns the updated record including current frequency."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT frequency FROM coverage_gaps WHERE crop_submitted = $1",
            crop_submitted,
        )
        if existing:
            new_freq = existing["frequency"] + 1
            priority = _compute_priority(new_freq)
            await conn.execute(
                """
                UPDATE coverage_gaps
                SET frequency = $1, last_reported_at = NOW(), priority = $2
                WHERE crop_submitted = $3
                """,
                new_freq, priority, crop_submitted,
            )
            return {"crop_submitted": crop_submitted, "frequency": new_freq, "priority": priority}
        else:
            await conn.execute(
                """
                INSERT INTO coverage_gaps
                    (crop_submitted, category_guess, frequency, first_reported_by, priority)
                VALUES ($1, $2, 1, $3, 'low')
                """,
                crop_submitted, category_guess, reported_by,
            )
            return {"crop_submitted": crop_submitted, "frequency": 1, "priority": "low"}


async def get_gap_summary() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT crop_submitted, category_guess, frequency, priority, status,
                   first_reported_at, last_reported_at
            FROM coverage_gaps
            ORDER BY frequency DESC
            """
        )
    return [dict(r) for r in rows]
