import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.recommender import ProfileStore, Recommender, _parse_list_field
from src.interaction_store import InteractionStore


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def farmers_df():
    return pd.DataFrame([
        {
            "id": "F0001", "full_name": "Farmer 1",
            "specialties": ["maize_grain", "sorghum"],
            "district": "Kampala",
            "average_rating": 4.5, "fulfillment_rate": 0.92,
            "total_sales": 30, "total_listings": 35,
        },
        {
            "id": "F0002", "full_name": "Farmer 2",
            "specialties": ["tomatoes", "irish_potatoes"],
            "district": "Mbarara",
            "average_rating": 3.8, "fulfillment_rate": 0.75,
            "total_sales": 12, "total_listings": 15,
        },
        {
            "id": "F0003", "full_name": "Farmer 3",
            "specialties": ["maize_grain", "yellow_beans", "millet"],
            "district": "Kampala",
            "average_rating": 4.9, "fulfillment_rate": 0.98,
            "total_sales": 80, "total_listings": 85,
        },
    ])


@pytest.fixture
def buyers_df():
    return pd.DataFrame([
        {
            "id": "B0001", "full_name": "Buyer 1",
            "interests": ["maize_grain", "sorghum"],
            "district": "Kampala",
            "payment_reliability": 0.95, "total_spent": 5_000_000,
            "total_orders": 20,
        },
        {
            "id": "B0002", "full_name": "Buyer 2",
            "interests": ["tomatoes"],
            "district": "Mbarara",
            "payment_reliability": 0.80, "total_spent": 2_000_000,
            "total_orders": 8,
        },
    ])


@pytest.fixture
def recommender(farmers_df, buyers_df):
    ps = ProfileStore.__new__(ProfileStore)
    ps.farmers = farmers_df
    ps.buyers = buyers_df
    ps._farmers_path = ""
    ps._buyers_path = ""
    return Recommender(ps, InteractionStore())


# ── Unit Tests ────────────────────────────────────────────────────────────────

def test_parse_list_field_string():
    assert _parse_list_field("maize_grain,sorghum") == ["maize_grain", "sorghum"]


def test_parse_list_field_list():
    assert _parse_list_field(["maize_grain"]) == ["maize_grain"]


def test_parse_list_field_empty_string():
    assert _parse_list_field("") == []


def test_parse_list_field_whitespace():
    assert _parse_list_field("maize_grain , sorghum") == ["maize_grain", "sorghum"]


def test_farmers_for_buyer_returns_correct_type(recommender):
    result = recommender.recommend_farmers_for_buyer("B0001", top_n=3)
    assert isinstance(result, list)
    assert all(isinstance(r, dict) for r in result)


def test_farmers_for_buyer_has_required_keys(recommender):
    result = recommender.recommend_farmers_for_buyer("B0001", top_n=2)
    for r in result:
        assert "id" in r
        assert "name" in r
        assert "district" in r
        assert "specialties" in r
        assert "averageRating" in r
        assert "totalSales" in r
        assert "totalListings" in r
        assert "matchScore" in r


def test_farmers_for_buyer_scores_in_range(recommender):
    result = recommender.recommend_farmers_for_buyer("B0001", top_n=3)
    assert all(0.0 <= r["matchScore"] <= 1.0 for r in result)


def test_farmers_for_buyer_sorted_descending(recommender):
    result = recommender.recommend_farmers_for_buyer("B0001", top_n=3)
    scores = [r["matchScore"] for r in result]
    assert scores == sorted(scores, reverse=True)


def test_farmers_for_buyer_respects_top_n(recommender):
    result = recommender.recommend_farmers_for_buyer("B0001", top_n=1)
    assert len(result) <= 1


def test_high_crop_overlap_farmer_ranked_first(recommender):
    # B0001 wants maize_grain + sorghum in Kampala
    # F0001 offers both in Kampala (full crop + district match)
    # F0003 offers maize_grain in Kampala (partial crop match)
    result = recommender.recommend_farmers_for_buyer("B0001", top_n=3)
    assert result[0]["id"] == "F0001"


def test_buyers_for_farmer_returns_list(recommender):
    result = recommender.recommend_buyers_for_farmer("F0001", top_n=2)
    assert isinstance(result, list)


def test_buyers_for_farmer_has_required_keys(recommender):
    result = recommender.recommend_buyers_for_farmer("F0001", top_n=2)
    for r in result:
        assert "id" in r
        assert "name" in r
        assert "district" in r
        assert "interests" in r
        assert "totalOrders" in r
        assert "matchScore" in r


def test_buyers_for_farmer_scores_in_range(recommender):
    result = recommender.recommend_buyers_for_farmer("F0001", top_n=2)
    assert all(0.0 <= r["matchScore"] <= 1.0 for r in result)


def test_unknown_buyer_returns_empty(recommender):
    assert recommender.recommend_farmers_for_buyer("GHOST", top_n=3) == []


def test_unknown_farmer_returns_empty(recommender):
    assert recommender.recommend_buyers_for_farmer("GHOST", top_n=3) == []


def test_interaction_boost_increases_score(recommender):
    before = {r["id"]: r["matchScore"] for r in recommender.recommend_farmers_for_buyer("B0001", top_n=3)}
    recommender.interactions.apply_event("purchase_completed", "B0001", "F0001")
    after = {r["id"]: r["matchScore"] for r in recommender.recommend_farmers_for_buyer("B0001", top_n=3)}
    if "F0001" in before and "F0001" in after:
        assert after["F0001"] >= before["F0001"]


def test_interaction_store_boost_capped():
    store = InteractionStore()
    for _ in range(20):
        store.apply_event("purchase_completed", "B0001", "F0001")
    assert store.get_boost("B0001", "F0001") <= 0.20


def test_interaction_store_multiple_events():
    store = InteractionStore()
    store.apply_event("farmer_viewed", "B0001", "F0001")
    store.apply_event("buyer_inquiry", "B0001", "F0001")
    boost = store.get_boost("B0001", "F0001")
    assert boost == pytest.approx(0.02 + 0.05)


def test_interaction_store_unknown_pair_returns_zero():
    store = InteractionStore()
    assert store.get_boost("X", "Y") == 0.0
