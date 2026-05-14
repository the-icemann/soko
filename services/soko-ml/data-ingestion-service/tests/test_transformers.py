"""
Unit tests for the data-ingestion-service transformer layer.
No DB, no Kafka, no HTTP — pure function tests.
"""
import pytest

from src.transformers.farmer_transformer import (
    CROP_NAME_NORMALISER,
    DISTRICT_COORDINATES,
    DISTRICT_TO_MARKET,
    normalise_crop,
    district_to_coords,
    transform_farmer,
)
from src.transformers.buyer_transformer import normalise_interest, transform_buyer
from src.transformers.price_transformer import (
    normalise_crop_from_order,
    normalise_market,
    transform_transaction_event,
)


# ── CROP_NAME_NORMALISER ──────────────────────────────────────────────────────

class TestNormaliseCrop:
    def test_canonical_keys_pass_through(self):
        assert normalise_crop("maize_grain") == "maize_grain"
        assert normalise_crop("yellow_beans") == "yellow_beans"

    def test_common_aliases(self):
        assert normalise_crop("maize") == "maize_grain"
        assert normalise_crop("corn") == "maize_grain"
        assert normalise_crop("posho") == "maize_grain"
        assert normalise_crop("beans") == "yellow_beans"
        assert normalise_crop("potatoes") == "irish_potatoes"
        assert normalise_crop("banana") == "matoke"
        assert normalise_crop("cassava") == "cassava_chips"
        assert normalise_crop("peanuts") == "groundnuts"
        assert normalise_crop("soya beans") == "soybeans"

    def test_case_insensitive(self):
        assert normalise_crop("Maize") == "maize_grain"
        assert normalise_crop("COFFEE") == "coffee"
        assert normalise_crop("Tomatoes") == "tomatoes"

    def test_unknown_crop_passes_through(self):
        assert normalise_crop("moringa") == "moringa"
        assert normalise_crop("avocado") == "avocado"

    def test_whitespace_stripped(self):
        assert normalise_crop("  maize  ") == "maize_grain"

    def test_all_normaliser_values_are_lowercase_underscore(self):
        for val in CROP_NAME_NORMALISER.values():
            assert " " not in val, f"Value '{val}' contains space"
            assert val == val.lower(), f"Value '{val}' is not lowercase"


# ── DISTRICT COORDINATES ──────────────────────────────────────────────────────

class TestDistrictCoords:
    def test_known_district(self):
        lat, lng = district_to_coords("Kampala")
        assert abs(lat - 0.3476) < 0.001
        assert abs(lng - 32.5825) < 0.001

    def test_unknown_district_returns_none(self):
        lat, lng = district_to_coords("Atlantis")
        assert lat is None
        assert lng is None

    def test_all_coordinates_plausible_for_uganda(self):
        for district, (lat, lng) in DISTRICT_COORDINATES.items():
            assert -2.0 <= lat <= 4.5, f"{district} lat {lat} out of Uganda range"
            assert 29.5 <= lng <= 35.0, f"{district} lng {lng} out of Uganda range"


# ── DISTRICT_TO_MARKET ────────────────────────────────────────────────────────

class TestDistrictToMarket:
    def test_kampala_maps_to_kisenyi(self):
        assert DISTRICT_TO_MARKET["Kampala"] == "Kisenyi_Kampala"

    def test_gulu_maps_to_gulu(self):
        assert DISTRICT_TO_MARKET["Gulu"] == "Gulu"

    def test_all_market_ids_are_non_empty_strings(self):
        for district, market_id in DISTRICT_TO_MARKET.items():
            assert isinstance(market_id, str) and market_id, f"{district} has empty market"


# ── TRANSFORM_FARMER ──────────────────────────────────────────────────────────

class TestTransformFarmer:
    BASE_PAYLOAD = {
        "id": "F001",
        "name": "Alice Nakato",
        "district": "Kampala",
        "village": "Mulago",
        "specialties": "maize,beans,tomatoes",
        "average_rating": 4.2,
        "response_time_hours": 6,
        "is_verified": True,
    }

    def test_basic_transform(self):
        result = transform_farmer(self.BASE_PAYLOAD)
        assert result["farmer_id"] == "F001"
        assert result["name"] == "Alice Nakato"
        assert result["district"] == "Kampala"
        assert "maize_grain" in result["crops_offered"]
        assert "yellow_beans" in result["crops_offered"]
        assert "tomatoes" in result["crops_offered"]

    def test_lat_lng_from_district_centroid(self):
        result = transform_farmer(self.BASE_PAYLOAD)
        expected_lat, expected_lng = DISTRICT_COORDINATES["Kampala"]
        assert abs(result["lat"] - expected_lat) < 0.001
        assert abs(result["lng"] - expected_lng) < 0.001

    def test_rating_clamped_to_zero_when_missing(self):
        payload = {**self.BASE_PAYLOAD, "average_rating": None}
        result = transform_farmer(payload)
        assert result["avg_rating"] == 0.0

    def test_response_time_string_parsed(self):
        payload = {**self.BASE_PAYLOAD, "responseTime": "12h"}
        result = transform_farmer(payload)
        assert result["avg_response_time_hrs"] == 12.0


# ── BUYER TRANSFORMER ─────────────────────────────────────────────────────────

class TestBuyerTransformer:
    def test_normalise_grains_category(self):
        crops = normalise_interest("Grains")
        assert "maize_grain" in crops
        assert "sorghum" in crops

    def test_normalise_vegetables(self):
        crops = normalise_interest("Vegetables")
        assert "tomatoes" in crops or "kale" in crops

    def test_unknown_interest_passes_through(self):
        crops = normalise_interest("Moringa")
        assert "moringa" in crops

    def test_transform_buyer_basic(self):
        payload = {
            "id": "B001",
            "name": "John Ssemakula",
            "district": "Wakiso",
            "interests": "Grains,Legumes",
        }
        result = transform_buyer(payload)
        assert result["buyer_id"] == "B001"
        assert "maize_grain" in result["preferred_crops"]
        assert "yellow_beans" in result["preferred_crops"]


# ── PRICE TRANSFORMER ─────────────────────────────────────────────────────────

class TestPriceTransformer:
    def test_normalise_crop_from_product_name(self):
        crop = normalise_crop_from_order(product_name="Maize (Dry)", category="Grains")
        assert crop == "maize_grain"

    def test_falls_back_to_category_mapping(self):
        crop = normalise_crop_from_order(product_name="", category="Grains")
        assert crop is not None

    def test_normalise_market_kampala(self):
        market = normalise_market("Kampala")
        assert market == "Kisenyi_Kampala"

    def test_normalise_market_unknown_returns_none(self):
        # Unknown districts return None — callers skip them rather than
        # mis-attributing prices to a default market.
        market = normalise_market("Atlantis")
        assert market is None

    def test_normalise_market_case_insensitive_via_title(self):
        assert normalise_market("gulu") == "Gulu"

    def test_transform_transaction_event_valid(self):
        # Event shape mirrors what order-service publishes to soko.transactions.
        # price_per_kg_ugx is the UGX-per-kg price — never USD.
        event = {
            "event_type": "purchase_completed",
            "order_id": "ORD-001",
            "product_name": "Beans",
            "crop": "Legumes",
            "market": "Gulu",
            "price_per_kg_ugx": 1500.0,
            "quantity_kg": 100,
            "farmer_id": "F001",
            "buyer_id": "B001",
            "timestamp": "2026-05-14T10:00:00Z",
        }
        result = transform_transaction_event(event)
        assert result is not None
        assert result["crop"] == "yellow_beans"
        assert result["market"] == "Gulu"
        assert float(result["price_per_kg"]) == 1500.0
        assert result["currency"] == "UGX"

    def test_transform_transaction_event_skips_unmappable_market(self):
        # "Atlantis" is not in DISTRICT_TO_MARKET → normalise_market returns None → skipped
        event = {
            "event_type": "purchase_completed",
            "order_id": "ORD-002",
            "product_name": "Maize",
            "crop": "Grains",
            "market": "Atlantis",
            "price_per_kg_ugx": 1300.0,
            "quantity_kg": 50,
            "farmer_id": "F001",
            "buyer_id": "B001",
            "timestamp": "2026-05-14T10:00:00Z",
        }
        result = transform_transaction_event(event)
        assert result is None

    def test_transform_event_zero_price_returns_none(self):
        event = {
            "event_type": "purchase_completed",
            "order_id": "ORD-003",
            "product_name": "Maize",
            "crop": "Grains",
            "market": "Kampala",
            "price_per_kg_ugx": 0,
            "quantity_kg": 50,
            "farmer_id": "F001",
            "buyer_id": "B001",
            "timestamp": "2026-05-14T10:00:00Z",
        }
        result = transform_transaction_event(event)
        assert result is None

    def test_transform_event_non_purchase_returns_none(self):
        event = {
            "event_type": "order_cancelled",
            "order_id": "ORD-004",
            "product_name": "Maize",
            "crop": "Grains",
            "market": "Kampala",
            "price_per_kg_ugx": 1300.0,
            "quantity_kg": 50,
            "timestamp": "2026-05-14T10:00:00Z",
        }
        result = transform_transaction_event(event)
        assert result is None
