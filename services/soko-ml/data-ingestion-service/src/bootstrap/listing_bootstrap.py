"""
Scans active listings from produce-service to seed the coverage map
with what crops are actually being sold, even before orders arrive.
"""
import logging

from ..clients.listing_client import fetch_all_listings
from ..transformers.farmer_transformer import CROP_NAME_NORMALISER, DISTRICT_TO_MARKET, DEFAULT_MARKET
from ..feature_store import get_pool

log = logging.getLogger(__name__)

CATEGORY_TO_CROP: dict[str, list[str]] = {
    "Grains":     ["maize_grain", "sorghum", "millet"],
    "Vegetables": ["tomatoes", "kale"],
    "Fruits":     ["matoke"],
    "Tubers":     ["irish_potatoes", "cassava_chips"],
    "Legumes":    ["yellow_beans"],
}


async def bootstrap_listings() -> int:
    """
    For each active listing, ensure the crop-market pair exists in coverage_map.
    Does not insert price observations — that's done from real orders.
    """
    pairs_seen: set[tuple[str, str]] = set()

    async for listing in fetch_all_listings():
        name     = listing.get("name", "")
        category = listing.get("category", "")
        district = listing.get("district", "")

        market = DISTRICT_TO_MARKET.get(district, DEFAULT_MARKET)

        # Try to identify specific crop from product name first
        norm_name = CROP_NAME_NORMALISER.get(name.lower().strip())
        if norm_name:
            pairs_seen.add((norm_name, market))
        else:
            # Fall back to all crops in this category
            for crop in CATEGORY_TO_CROP.get(category, []):
                pairs_seen.add((crop, market))

    if not pairs_seen:
        log.info("No active listings found — coverage map already seeded from schema.sql")
        return 0

    pool = await get_pool()
    count = 0
    async with pool.acquire() as conn:
        for crop, market in pairs_seen:
            await conn.execute(
                """
                INSERT INTO coverage_map (crop, market)
                VALUES ($1, $2)
                ON CONFLICT (crop, market) DO NOTHING
                """,
                crop, market,
            )
            count += 1

    log.info(f"Listing bootstrap complete: {count} coverage_map pairs ensured")
    return count


async def bootstrap_market_bootstrap() -> None:
    """No-op: market registry is seeded in schema.sql at db-init time."""
    log.info("Market registry loaded from schema.sql — no runtime bootstrap needed")
