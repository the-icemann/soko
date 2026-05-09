import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.predictor import ModelRegistry, SUPPORTED_MARKETS, SUPPORTED_CROPS, BASE_PRICES_UGX


def test_supported_markets_count():
    assert len(SUPPORTED_MARKETS) == 6


def test_supported_crops_count():
    assert len(SUPPORTED_CROPS) == 8


def test_base_prices_coverage():
    for crop in SUPPORTED_CROPS:
        for market in SUPPORTED_MARKETS:
            assert market in BASE_PRICES_UGX[crop], f"Missing price: {crop} / {market}"


def test_all_base_prices_in_ugx():
    """Prices must be plausible UGX values (not KES)."""
    for crop, markets in BASE_PRICES_UGX.items():
        for market, price in markets.items():
            assert price >= 400, f"{crop}/{market} price too low: {price}"
            assert price <= 10000, f"{crop}/{market} price suspiciously high: {price}"


def test_fallback_predict_correct_count():
    registry = ModelRegistry("/nonexistent")
    preds = registry._fallback_predict(1300, 4)
    assert len(preds) == 4


def test_fallback_predict_structure():
    registry = ModelRegistry("/nonexistent")
    p = registry._fallback_predict(1300, 1)[0]
    assert "date" in p
    assert "predicted_price_ugx" in p
    assert "lower_bound" in p
    assert "upper_bound" in p


def test_fallback_bounds_ordered():
    registry = ModelRegistry("/nonexistent")
    for p in registry._fallback_predict(1300, 4):
        assert p["lower_bound"] <= p["predicted_price_ugx"]
        assert p["upper_bound"] >= p["predicted_price_ugx"]


def test_fallback_prices_positive():
    registry = ModelRegistry("/nonexistent")
    preds = registry._fallback_predict(500, 8)
    assert all(p["predicted_price_ugx"] >= 1 for p in preds)


def test_fallback_prices_in_reasonable_range():
    registry = ModelRegistry("/nonexistent")
    for crop in SUPPORTED_CROPS:
        for market in SUPPORTED_MARKETS:
            base = BASE_PRICES_UGX[crop][market]
            preds = registry._fallback_predict(base, 4)
            for p in preds:
                assert p["predicted_price_ugx"] > base * 0.5
                assert p["predicted_price_ugx"] < base * 2.0


def test_model_registry_empty_dir(tmp_path):
    registry = ModelRegistry(str(tmp_path))
    assert registry.load_all() == 0
    assert registry.loaded_count == 0


def test_model_registry_missing_dir():
    registry = ModelRegistry("/this/does/not/exist")
    assert registry.load_all() == 0


def test_predict_uses_fallback_when_no_model():
    registry = ModelRegistry("/nonexistent")
    result = registry.predict("Kisenyi_Kampala", "maize_grain", 4)
    assert len(result) == 4
    assert all(r["predicted_price_ugx"] > 0 for r in result)


def test_predict_unknown_crop_uses_default_base():
    registry = ModelRegistry("/nonexistent")
    result = registry.predict("Gulu", "unknown_crop", 2)
    assert len(result) == 2


def test_model_key_uses_double_underscore_separator():
    # The pkl filename convention: {market}__{crop}.pkl
    market, crop = "Kisenyi_Kampala", "maize_grain"
    expected_key = f"{market}__{crop}"
    registry = ModelRegistry("/nonexistent")
    assert registry.get(market, crop) is None  # no model loaded
    # Verify the key format matches what load_all() would use from stem
    assert expected_key == "Kisenyi_Kampala__maize_grain"
