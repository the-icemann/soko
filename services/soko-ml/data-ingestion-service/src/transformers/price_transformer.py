"""
Converts order-service order payloads into ML price_observation records.
"""
from datetime import datetime, date
from typing import Optional

from .farmer_transformer import CROP_NAME_NORMALISER, DISTRICT_TO_MARKET


def normalise_crop_from_order(product_name: str, category: str) -> str:
    """
    Determine the ML crop key from an order item.
    Priority: product_name (specific) → category (broad fallback).
    """
    if product_name:
        key = product_name.lower().strip()
        if key in CROP_NAME_NORMALISER:
            return CROP_NAME_NORMALISER[key]
        for word in key.split():
            if word in CROP_NAME_NORMALISER:
                return CROP_NAME_NORMALISER[word]

    cat_map = {
        "grains":     "maize_grain",
        "vegetables": "tomatoes",
        "fruits":     "matoke",
        "tubers":     "irish_potatoes",
        "legumes":    "yellow_beans",
        "herbs":      None,
        "dairy":      None,
        "poultry":    None,
        "livestock":  None,
        "fish":       None,
        "other":      None,
    }
    cat_key = category.lower().strip()
    crop = cat_map.get(cat_key)
    if crop:
        return crop

    return CROP_NAME_NORMALISER.get(product_name.lower().strip(), product_name.lower().strip() or "unknown")


def normalise_market(district: str) -> Optional[str]:
    """
    Maps a delivery district string to an ML market node ID.
    Returns None for districts not in DISTRICT_TO_MARKET — callers should skip
    such events rather than mis-attributing them to a default market.
    """
    return DISTRICT_TO_MARKET.get(district) or DISTRICT_TO_MARKET.get(district.title())


def transform_transaction_event(event: dict) -> Optional[dict]:
    """
    Converts a soko.transactions Kafka event (purchase_completed) into a
    price_observations record. Returns None if the event should be skipped.

    The order-service publishes price_per_kg_ugx (UGX per kg) directly.
    All monetary values stored in UGX — never USD.
    """
    if event.get("event_type") != "purchase_completed":
        return None

    price_per_kg = float(event.get("price_per_kg_ugx", 0))
    if price_per_kg <= 0:
        return None

    quantity_kg  = float(event.get("quantity_kg", 0))
    product_name = event.get("product_name", "")
    category     = event.get("crop", "")       # soko.transactions uses 'crop' for category

    crop   = normalise_crop_from_order(product_name, category)
    market = normalise_market(event.get("market", ""))

    if not crop or crop == "unknown":
        return None
    if not market:
        return None

    raw_ts = event.get("timestamp", "")
    try:
        observed_at = datetime.fromisoformat(raw_ts.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        observed_at = date.today()

    return {
        "observed_at": observed_at,
        "market":      market,
        "crop":        crop,
        "price_per_kg": price_per_kg,
        "currency":    "UGX",
        "source":      "soko_order",
        "order_id":    event.get("order_id"),
        "quantity_kg": quantity_kg if quantity_kg > 0 else None,
    }


def transform_order_item(
    order_id: str,
    item: dict,
    delivery_district: str,
    completed_at: str,
) -> Optional[dict]:
    """
    Converts an order item from the order bootstrap API into a price_observations record.
    Used during bootstrap, not streaming.
    """
    price_per_kg = float(item.get("unit_price", 0))
    if price_per_kg <= 0:
        return None

    product_name = item.get("product_name", "")
    category     = item.get("category", "")

    crop   = normalise_crop_from_order(product_name, category)
    market = normalise_market(delivery_district)

    if crop == "unknown" or not crop:
        return None

    try:
        observed_at = datetime.fromisoformat(completed_at.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        observed_at = date.today()

    return {
        "observed_at": observed_at,
        "market":      market,
        "crop":        crop,
        "price_per_kg": price_per_kg,
        "currency":    "UGX",
        "source":      "soko_order",
        "order_id":    order_id,
        "quantity_kg": float(item.get("quantity", 0)) or None,
    }
