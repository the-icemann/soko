"""
Publishes coverage gap events to soko.ml.events and records them in coverage_gaps.
Called by fallback.py when Tier 3 triggers.
"""
import json
import logging
import os
from datetime import datetime
from typing import Optional

import asyncpg
from confluent_kafka import Producer

log = logging.getLogger(__name__)

POSTGRES_DSN      = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@soko-ml-db:5432/soko_ml_db")
BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
ML_EVENTS_TOPIC   = os.getenv("KAFKA_ML_EVENTS_TOPIC", "soko.ml.events")
GAPS_TOPIC        = os.getenv("KAFKA_GAPS_TOPIC", "soko.gaps")

CATEGORY_GUESSES: dict[str, str] = {
    "nakati": "vegetables", "kale": "vegetables", "cabbage": "vegetables",
    "onions": "vegetables", "eggplant": "vegetables",
    "groundnuts": "legumes", "soybeans": "legumes",
    "sweet_potatoes": "tubers", "yams": "tubers",
    "rice": "cereals", "wheat": "cereals",
    "coffee": "cash_crops", "vanilla": "cash_crops", "cotton": "cash_crops",
}

PRIORITY_THRESHOLDS = {"low": (1, 5), "medium": (5, 15), "high": (15, 99999)}

_pool: Optional[asyncpg.Pool] = None
_producer: Optional[Producer] = None


def _get_producer() -> Optional[Producer]:
    global _producer
    if _producer is None:
        try:
            _producer = Producer({
                "bootstrap.servers": BOOTSTRAP_SERVERS,
                "socket.timeout.ms": 3000,
            })
        except Exception as exc:
            log.warning(f"Kafka producer init failed: {exc}")
    return _producer


def _compute_priority(freq: int) -> str:
    for p, (lo, hi) in PRIORITY_THRESHOLDS.items():
        if lo <= freq < hi:
            return p
    return "high"


async def record_and_notify_gap(crop_submitted: str, farmer_id: str) -> None:
    """Records the gap in Postgres and publishes two Kafka events (farmer + admin)."""
    category = CATEGORY_GUESSES.get(crop_submitted.lower(), "other")

    # Upsert into coverage_gaps
    try:
        pool = await _get_db_pool()
        async with pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT frequency FROM coverage_gaps WHERE crop_submitted = $1",
                crop_submitted,
            )
            if existing:
                new_freq = existing["frequency"] + 1
                priority = _compute_priority(new_freq)
                await conn.execute(
                    "UPDATE coverage_gaps SET frequency=$1, last_reported_at=NOW(), priority=$2 WHERE crop_submitted=$3",
                    new_freq, priority, crop_submitted,
                )
            else:
                new_freq = 1
                priority = "low"
                await conn.execute(
                    "INSERT INTO coverage_gaps (crop_submitted, category_guess, frequency, first_reported_by, priority) VALUES ($1,$2,1,$3,'low')",
                    crop_submitted, category, farmer_id,
                )
    except Exception as exc:
        log.warning(f"Failed to record coverage gap in DB: {exc}")
        new_freq = 1
        priority = "low"

    ts = datetime.utcnow().isoformat() + "Z"

    farmer_event = json.dumps({
        "event_type":     "crop_coverage_gap",
        "recipient":      "farmer",
        "farmer_id":      farmer_id,
        "crop_submitted": crop_submitted,
        "timestamp":      ts,
        "message": {
            "title":  "We're working on adding this crop",
            "body":   (
                f"We don't have market intelligence for {crop_submitted} yet. "
                "Our team has been notified and will add coverage soon. "
                "You can still list your produce and transact normally on Soko."
            ),
            "action": "continue_listing",
        },
    }).encode()

    admin_event = json.dumps({
        "event_type":       "crop_coverage_gap",
        "recipient":        "admin",
        "crop_submitted":   crop_submitted,
        "category_guess":   category,
        "first_reported_by": farmer_id,
        "frequency":        new_freq,
        "status":           "pending_review",
        "priority":         priority,
        "timestamp":        ts,
    }).encode()

    producer = _get_producer()
    if producer:
        try:
            producer.produce(GAPS_TOPIC, key=crop_submitted.encode(), value=farmer_event)
            producer.produce(GAPS_TOPIC, key=crop_submitted.encode(), value=admin_event)
            producer.poll(0)
            log.info(f"coverage_gap_events_published crop={crop_submitted} priority={priority}")
        except Exception as exc:
            log.warning(f"Failed to publish gap events: {exc}")


async def _get_db_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(POSTGRES_DSN, min_size=1, max_size=3)
    return _pool
