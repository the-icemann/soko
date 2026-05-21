"""
Pulls all farmer and buyer profiles from user-service and populates the feature store.
Called once at startup (or on make ingest-bootstrap) when tables are empty.
"""
import logging

from ..clients.user_client import fetch_all_farmers, fetch_all_buyers
from ..transformers.farmer_transformer import transform_farmer
from ..transformers.buyer_transformer import transform_buyer
from ..feature_store import bulk_upsert_farmers, bulk_upsert_buyers

log = logging.getLogger(__name__)


async def bootstrap_farmers() -> int:
    records = []
    async for profile in fetch_all_farmers():
        try:
            records.append(transform_farmer(profile))
        except Exception as exc:
            log.warning(f"Could not transform farmer {profile.get('id')}: {exc}")

    if not records:
        log.warning("No farmer profiles returned from user-service — skipping farmer bootstrap")
        return 0

    count = await bulk_upsert_farmers(records)
    log.info(f"Farmer bootstrap complete: {count}/{len(records)} upserted")
    return count


async def bootstrap_buyers() -> int:
    records = []
    async for profile in fetch_all_buyers():
        try:
            records.append(transform_buyer(profile))
        except Exception as exc:
            log.warning(f"Could not transform buyer {profile.get('id')}: {exc}")

    if not records:
        log.warning("No buyer profiles returned from user-service — skipping buyer bootstrap")
        return 0

    count = await bulk_upsert_buyers(records)
    log.info(f"Buyer bootstrap complete: {count}/{len(records)} upserted")
    return count
