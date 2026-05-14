"""
asyncpg client for reading price_observations from soko_ml_db.
Used by the training pipeline to load real transaction data instead of CSV files.
"""
import logging
import os
from typing import Optional

import asyncpg
import pandas as pd

log = logging.getLogger(__name__)

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@soko-ml-db:5432/soko_ml_db")
MIN_OBSERVATIONS = int(os.getenv("MIN_OBSERVATIONS_FOR_MODEL", "30"))

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def fetch_training_data(market: str, crop: str) -> tuple[pd.DataFrame, str]:
    """
    Returns (DataFrame with columns [ds, y], source_label).
    source_label is 'soko_order' when using real data, 'farmgain_seed' when falling back.
    Falls back to seed data if fewer than MIN_OBSERVATIONS real records exist.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT observed_at AS ds, price_per_kg AS y
            FROM price_observations
            WHERE market = $1 AND crop = $2
              AND source = 'soko_order'
            ORDER BY observed_at ASC
            """,
            market, crop,
        )

    if len(rows) >= MIN_OBSERVATIONS:
        df = pd.DataFrame([{"ds": r["ds"], "y": float(r["y"])} for r in rows])
        log.info(f"Training data loaded from feature store: {market}/{crop} ({len(df)} rows)")
        return df, "soko_order"

    if len(rows) > 0:
        log.warning(
            f"Insufficient real data for {market}/{crop}, using seed fallback "
            f"({len(rows)} observations — need {MIN_OBSERVATIONS})"
        )
    else:
        log.warning(f"No real data for {market}/{crop}, using seed fallback")

    return pd.DataFrame(), "farmgain_seed"


async def get_coverage_status(market: str, crop: str) -> Optional[dict]:
    """Returns coverage_map row for this pair, or None if not tracked."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT observation_count, is_model_ready FROM coverage_map WHERE crop = $1 AND market = $2",
            crop, market,
        )
    return dict(row) if row else None
