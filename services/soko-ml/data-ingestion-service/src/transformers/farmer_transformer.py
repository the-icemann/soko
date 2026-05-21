"""
Converts user-service farmer profile payloads into ML farmer_features records.
"""
from datetime import datetime
from typing import Optional

# Maps raw crop name variants (from profile specialties) to ML crop keys.
# Unknown names pass through as-is and trigger the coverage gap check downstream.
CROP_NAME_NORMALISER: dict[str, str] = {
    "maize":           "maize_grain",
    "corn":            "maize_grain",
    "posho":           "maize_grain",
    "maize grain":     "maize_grain",
    "maize_grain":     "maize_grain",
    "beans":           "yellow_beans",
    "yellow beans":    "yellow_beans",
    "yellow_beans":    "yellow_beans",
    "potatoes":        "irish_potatoes",
    "irish potatoes":  "irish_potatoes",
    "irish_potatoes":  "irish_potatoes",
    "cooking banana":  "matoke",
    "banana":          "matoke",
    "matoke":          "matoke",
    "cassava":         "cassava_chips",
    "cassava chips":   "cassava_chips",
    "cassava_chips":   "cassava_chips",
    "millet":          "millet",
    "sorghum":         "sorghum",
    "tomato":          "tomatoes",
    "tomatoes":        "tomatoes",
    "coffee":          "coffee",
    "vanilla":         "vanilla",
    "cotton":          "cotton",
    "groundnuts":      "groundnuts",
    "peanuts":         "groundnuts",
    "soybeans":        "soybeans",
    "soya beans":      "soybeans",
    "sunflower":       "sunflower",
    "rice":            "rice",
    "sweet potatoes":  "sweet_potatoes",
    "yams":            "yams",
    "onions":          "onions",
    "cabbage":         "cabbage",
    "kale":            "kale",
    "nakati":          "nakati",
    "eggplant":        "eggplant",
    "aubergine":       "eggplant",
}

# Approximate GPS centroids for Uganda districts.
# Used as best-available location when user profiles have no lat/lng field.
DISTRICT_COORDINATES: dict[str, tuple[float, float]] = {
    "Kampala":     (0.3476,  32.5825),
    "Wakiso":      (0.4040,  32.4591),
    "Mukono":      (0.3536,  32.7552),
    "Jinja":       (0.4244,  33.2041),
    "Mbarara":     (-0.6072, 30.6545),
    "Gulu":        (2.7747,  32.2990),
    "Lira":        (2.2499,  32.8998),
    "Mbale":       (1.0824,  34.1754),
    "Masaka":      (-0.3390, 31.7369),
    "Arua":        (3.0200,  30.9100),
    "Fort Portal": (0.6710,  30.2750),
    "Soroti":      (1.7150,  33.6110),
    "Tororo":      (0.6920,  34.1800),
    "Kabale":      (-1.2490, 29.9900),
    "Hoima":       (1.4340,  31.3520),
    "Kasese":      (0.1830,  30.0860),
    "Iganga":      (0.6100,  33.4720),
    "Bushenyi":    (-0.5470, 30.1910),
    "Mityana":     (0.4280,  32.0420),
    "Mubende":     (0.5770,  31.3700),
    "Ntungamo":    (-0.8820, 30.2640),
    "Rukungiri":   (-0.8420, 29.9440),
    "Kyenjojo":    (0.6210,  30.6330),
    "Rakai":       (-0.7200, 31.4200),
    "Kiboga":      (0.9200,  31.7700),
}

# Approximate mapping from delivery district to the nearest ML market node.
# Used when transforming order data that only carries a district name.
DISTRICT_TO_MARKET: dict[str, str] = {
    "Kampala":     "Kisenyi_Kampala",
    "Wakiso":      "Kisenyi_Kampala",
    "Mukono":      "Kisenyi_Kampala",
    "Gulu":        "Gulu",
    "Mbarara":     "Mbarara",
    "Bushenyi":    "Mbarara",
    "Ntungamo":    "Mbarara",
    "Mbale":       "Mbale",
    "Tororo":      "Mbale",
    "Iganga":      "Mbale",
    "Lira":        "Lira",
    "Soroti":      "Lira",
    "Arua":        "Gulu",
    "Masaka":      "Masaka",
    "Rakai":       "Masaka",
    "Jinja":       "Kisenyi_Kampala",
}
DEFAULT_MARKET = "Kisenyi_Kampala"


def normalise_crop(raw: str) -> str:
    return CROP_NAME_NORMALISER.get(raw.lower().strip(), raw.lower().strip())


def district_to_coords(district: str) -> tuple[Optional[float], Optional[float]]:
    coords = DISTRICT_COORDINATES.get(district)
    if coords:
        return coords
    # Try case-insensitive lookup
    for k, v in DISTRICT_COORDINATES.items():
        if k.lower() == district.lower():
            return v
    return None, None


def transform_farmer(profile: dict) -> dict:
    """
    Converts a user-service FarmerProfile response dict into a farmer_features record.
    Input shape matches GET /users/farmers or GET /users/{id} responses.
    """
    farmer_id = profile.get("id", "")
    name      = profile.get("name", "")
    district  = profile.get("district", "")

    lat, lng = district_to_coords(district)

    raw_specialties = profile.get("specialties", [])
    if isinstance(raw_specialties, str):
        raw_specialties = [s.strip() for s in raw_specialties.split(",") if s.strip()]

    crops_offered = [normalise_crop(c) for c in raw_specialties if c]

    # Derive which markets this farmer likely serves from their district
    primary_market = DISTRICT_TO_MARKET.get(district, DEFAULT_MARKET)
    markets_served = list({primary_market})

    avg_rating       = float(profile.get("averageRating", 0.0) or 0.0)
    total_sales      = int(profile.get("totalSales", 0) or 0)
    total_listings   = int(profile.get("totalListings", 0) or 0)

    # fulfillment_rate not available from profile API — default to a neutral value
    fulfillment_rate = 1.0

    # response_time from profile is a string like "< 1 hr" — parse to hours
    avg_response_time_hrs = _parse_response_time(profile.get("responseTime"))

    return {
        "farmer_id":              farmer_id,
        "name":                   name,
        "lat":                    lat,
        "lng":                    lng,
        "district":               district,
        "crops_offered":          crops_offered,
        "markets_served":         markets_served,
        "avg_rating":             avg_rating,
        "fulfillment_rate":       fulfillment_rate,
        "avg_response_time_hrs":  avg_response_time_hrs,
        "total_orders_completed": total_sales,
        "total_orders_cancelled": 0,
        "total_listings":         total_listings,
        "last_active_at":         None,
    }


def _parse_response_time(value: Optional[str]) -> float:
    """Parse a response time string like '< 1 hr', '2 hrs', '24 hrs' into float hours."""
    if not value:
        return 24.0
    v = value.lower().replace("<", "").replace(">", "").strip()
    try:
        # Extract leading number
        import re
        match = re.search(r"(\d+\.?\d*)", v)
        if match:
            return float(match.group(1))
    except Exception:
        pass
    return 24.0
