"""
Market registry bootstrap — no-op because market data is seeded in schema.sql.
Module exists to maintain the file structure described in the system spec.
"""
import logging

log = logging.getLogger(__name__)


async def bootstrap_markets() -> int:
    log.info("Market registry is seeded in db/schema.sql — no HTTP bootstrap required")
    return 0
