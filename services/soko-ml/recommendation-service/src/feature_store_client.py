"""
asyncpg client for reading farmer_features and buyer_features from soko_ml_db.
Replaces CSV file loading in the recommendation-service.
"""
import logging
import os
from typing import Optional

import asyncpg
import pandas as pd

log = logging.getLogger(__name__)

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@soko-ml-db:5432/soko_ml_db")

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=2, max_size=8)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def load_farmers() -> pd.DataFrame:
    """
    Loads all farmer_features rows into a DataFrame.
    Returns empty DataFrame (with correct columns) if the table is empty.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                farmer_id   AS id,
                name        AS full_name,
                district,
                lat, lng,
                crops_offered   AS specialties,
                markets_served,
                avg_rating      AS average_rating,
                fulfillment_rate,
                avg_response_time_hrs,
                total_orders_completed  AS total_sales,
                total_orders_cancelled,
                total_listings,
                last_active_at
            FROM farmer_features
            """
        )

    if not rows:
        log.warning("farmer_features table is empty — recommendations will be empty until bootstrap runs")
        return pd.DataFrame(columns=[
            "id", "full_name", "district", "lat", "lng",
            "specialties", "markets_served", "average_rating",
            "fulfillment_rate", "avg_response_time_hrs",
            "total_sales", "total_listings",
        ])

    records = []
    for r in rows:
        rec = dict(r)
        # asyncpg returns TEXT[] as list already
        rec["specialties"]   = rec.get("specialties") or []
        rec["markets_served"] = rec.get("markets_served") or []
        records.append(rec)

    df = pd.DataFrame(records)
    log.info(f"farmers_loaded_from_feature_store count={len(df)}")
    return df


async def load_buyers() -> pd.DataFrame:
    """
    Loads all buyer_features rows into a DataFrame.
    Returns empty DataFrame if the table is empty.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                buyer_id    AS id,
                name        AS full_name,
                district,
                lat, lng,
                preferred_crops  AS interests,
                preferred_markets,
                avg_spend_per_order AS total_spent,
                payment_reliability,
                total_purchases  AS total_orders,
                last_active_at
            FROM buyer_features
            """
        )

    if not rows:
        log.warning("buyer_features table is empty — buyer recommendations will be empty")
        return pd.DataFrame(columns=[
            "id", "full_name", "district", "lat", "lng",
            "interests", "total_spent", "payment_reliability", "total_orders",
        ])

    records = []
    for r in rows:
        rec = dict(r)
        rec["interests"]         = rec.get("interests") or []
        rec["preferred_markets"] = rec.get("preferred_markets") or []
        records.append(rec)

    df = pd.DataFrame(records)
    log.info(f"buyers_loaded_from_feature_store count={len(df)}")
    return df
