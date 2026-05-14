"""
Converts user-service buyer profile payloads into ML buyer_features records.
"""
from .farmer_transformer import (
    CROP_NAME_NORMALISER, DISTRICT_TO_MARKET, DEFAULT_MARKET, district_to_coords
)


def normalise_interest(raw: str) -> list[str]:
    """
    Buyer interests are stored as categories (e.g. "Grains", "Vegetables").
    Expand each category to the known ML crops within it.
    Falls back to returning the interest as-is if unrecognised.
    """
    CATEGORY_CROPS: dict[str, list[str]] = {
        "grains":     ["maize_grain", "sorghum", "millet", "rice"],
        "vegetables": ["tomatoes", "kale", "nakati", "cabbage", "onions", "eggplant"],
        "fruits":     ["matoke"],
        "tubers":     ["irish_potatoes", "cassava_chips", "sweet_potatoes", "yams"],
        "legumes":    ["yellow_beans", "groundnuts", "soybeans"],
        "dairy":      [],
        "livestock":  [],
        "poultry":    [],
        "fish":       [],
        "other":      [],
    }
    key = raw.lower().strip()
    if key in CATEGORY_CROPS:
        return CATEGORY_CROPS[key]
    # Try normalising as a specific crop name
    normalised = CROP_NAME_NORMALISER.get(key, key)
    return [normalised] if normalised else []


def transform_buyer(profile: dict) -> dict:
    """
    Converts a user-service buyer profile response dict into a buyer_features record.
    Input shape matches GET /users/buyers (AuthenticatedUser schema).
    """
    buyer_id = profile.get("id", "")
    name     = profile.get("name", "")
    district = profile.get("district", "")

    lat, lng = district_to_coords(district)

    raw_interests = profile.get("interests", [])
    if isinstance(raw_interests, str):
        raw_interests = [i.strip() for i in raw_interests.split(",") if i.strip()]

    # Expand category interests to specific crop keys
    preferred_crops: list[str] = []
    for interest in raw_interests:
        preferred_crops.extend(normalise_interest(interest))
    preferred_crops = list(dict.fromkeys(preferred_crops))  # deduplicate, preserve order

    primary_market    = DISTRICT_TO_MARKET.get(district, DEFAULT_MARKET)
    preferred_markets = [primary_market]

    total_purchases  = int(profile.get("totalOrders", 0) or 0)
    total_spent      = float(profile.get("totalSpent", 0) or 0)
    avg_spend        = round(total_spent / max(total_purchases, 1), 2)

    # payment_reliability — not directly available; default to neutral
    payment_reliability = 1.0

    return {
        "buyer_id":               buyer_id,
        "name":                   name,
        "lat":                    lat,
        "lng":                    lng,
        "district":               district,
        "preferred_crops":        preferred_crops,
        "preferred_markets":      preferred_markets,
        "avg_order_volume_kg":    0.0,
        "payment_reliability":    payment_reliability,
        "avg_spend_per_order":    avg_spend,
        "total_purchases":        total_purchases,
        "last_active_at":         None,
    }
