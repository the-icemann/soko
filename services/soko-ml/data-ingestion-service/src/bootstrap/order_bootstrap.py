"""
Pulls all delivered orders from order-service and inserts price observations.
Falls back gracefully if the internal endpoint is not yet exposed.
"""
import logging

from ..clients.order_client import fetch_delivered_orders
from ..transformers.price_transformer import transform_order_item
from ..feature_store import bulk_insert_price_observations

log = logging.getLogger(__name__)


async def bootstrap_orders() -> int:
    records = []
    async for order in fetch_delivered_orders():
        order_id         = order.get("id", "")
        delivery_district = order.get("delivery_district", "")
        completed_at     = order.get("updated_at", "")  # closest to completion timestamp
        items            = order.get("items", [])

        for item in items:
            try:
                rec = transform_order_item(order_id, item, delivery_district, completed_at)
                if rec:
                    records.append(rec)
            except Exception as exc:
                log.warning(f"Could not transform order item {item.get('id')}: {exc}")

    if not records:
        log.info("No historical price observations from orders — will populate from live events")
        return 0

    count = await bulk_insert_price_observations(records)
    log.info(f"Order bootstrap complete: {count} price observations inserted")
    return count
