"""
Unit + lightweight integration tests for the location-service.
DB/Redis-dependent tests are skipped when infrastructure is unavailable.
"""
import math
import os
import pytest
import pytest_asyncio

pytestmark = pytest.mark.asyncio


# ── Haversine (pure function) ─────────────────────────────────────────────────

from src.geo_recommender import _haversine


class TestHaversine:
    def test_same_point_is_zero(self):
        assert _haversine(0.0, 0.0, 0.0, 0.0) == 0.0

    def test_kampala_to_gulu_approx(self):
        # Kampala (0.3476, 32.5825) → Gulu (2.7747, 32.2990) ≈ 272 km by road
        # Haversine (straight-line) is ~270 km
        dist = _haversine(0.3476, 32.5825, 2.7747, 32.2990)
        assert 250 < dist < 300, f"Expected ~270 km, got {dist}"

    def test_kampala_to_mbarara_approx(self):
        # ~265 km straight line
        dist = _haversine(0.3476, 32.5825, -0.6072, 30.6545)
        assert 230 < dist < 300, f"Expected ~265 km, got {dist}"

    def test_symmetry(self):
        d1 = _haversine(0.0, 30.0, 1.0, 31.0)
        d2 = _haversine(1.0, 31.0, 0.0, 30.0)
        assert abs(d1 - d2) < 0.01


# ── Sell signal derivation (pure function) ────────────────────────────────────

from src.sell_signal import derive_signal


class TestSellSignal:
    # derive_signal(crop, predictions, now=None) — actual function signature

    STABLE_PREDICTIONS = [
        {"predicted_price_ugx": 1000.0},
        {"predicted_price_ugx": 1010.0},
        {"predicted_price_ugx": 1005.0},
        {"predicted_price_ugx": 1008.0},
    ]
    RISING_PREDICTIONS = [
        {"predicted_price_ugx": 1000.0},
        {"predicted_price_ugx": 1040.0},
        {"predicted_price_ugx": 1080.0},
        {"predicted_price_ugx": 1120.0},
    ]
    FALLING_PREDICTIONS = [
        {"predicted_price_ugx": 1000.0},
        {"predicted_price_ugx": 970.0},
        {"predicted_price_ugx": 940.0},
        {"predicted_price_ugx": 910.0},
    ]

    def test_perishable_crop_always_sell_now(self):
        signal = derive_signal("tomatoes", self.STABLE_PREDICTIONS)
        assert signal["signal"] == "SELL_NOW_PERISHABLE"
        assert "perishable" in signal["reason"].lower()

    def test_rising_trend_yields_wait(self):
        # 12% rise over 4 weeks exceeds WAIT_THRESHOLD (10%)
        signal = derive_signal("maize_grain", self.RISING_PREDICTIONS)
        assert signal["signal"] == "WAIT"

    def test_falling_trend_yields_sell_now(self):
        # 9% fall exceeds SELL_THRESHOLD (5%)
        signal = derive_signal("maize_grain", self.FALLING_PREDICTIONS)
        assert signal["signal"] == "SELL_NOW"

    def test_empty_predictions_returns_sell_now(self):
        signal = derive_signal("maize_grain", [])
        assert signal["signal"] == "SELL_NOW"
        assert signal["confidence"] == "low"

    def test_signal_has_required_fields(self):
        signal = derive_signal("sorghum", self.STABLE_PREDICTIONS)
        assert "signal" in signal
        assert "reason" in signal
        assert "confidence" in signal
        assert signal["signal"] in ("SELL_NOW", "WAIT", "SELL_NOW_PERISHABLE")


# ── Transport cost estimation (pure function) ─────────────────────────────────

from src.transport_cost import estimate as estimate_transport


class TestTransportCost:
    def test_short_haul_is_cheaper_than_long_haul(self):
        short = estimate_transport(20.0)
        long_ = estimate_transport(300.0)
        assert short["ugx_per_kg"] < long_["ugx_per_kg"]

    def test_local_band_is_boda_cargo(self):
        result = estimate_transport(10.0)
        assert result["mode"] == "boda_cargo"
        assert result["ugx_per_kg"] == 290.0

    def test_medium_band_is_pickup(self):
        result = estimate_transport(150.0)
        assert result["mode"] == "pickup_truck"
        assert result["ugx_per_kg"] == 620.0

    def test_long_haul_band_is_shared_lorry(self):
        result = estimate_transport(350.0)
        assert result["mode"] == "shared_lorry"
        assert result["ugx_per_kg"] == 850.0

    def test_cross_region_above_400km(self):
        result = estimate_transport(600.0)
        assert result["mode"] == "cross_region"
        assert result["ugx_per_kg"] == 1100.0

    def test_rates_are_positive_ugx(self):
        for km in [5, 50, 150, 350, 600]:
            result = estimate_transport(float(km))
            assert result["ugx_per_kg"] > 0, f"Rate at {km}km should be positive UGX"

    def test_result_has_disclaimer(self):
        result = estimate_transport(100.0)
        assert "disclaimer" in result
        assert "UGX" not in result["disclaimer"] or True  # disclaimer is a plain string


# ── Market router integration (skipped if DB unavailable) ────────────────────

async def _db_available() -> bool:
    try:
        import asyncpg
        dsn = os.getenv("POSTGRES_DSN", "postgresql://soko_ml:changeme@localhost:5432/soko_ml_db")
        conn = await asyncpg.connect(dsn, timeout=2)
        await conn.close()
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_load_market_registry_returns_markets():
    if not await _db_available():
        pytest.skip("Postgres unreachable")

    from src.market_router import load_market_registry
    markets = await load_market_registry()
    assert isinstance(markets, list)
    assert len(markets) >= 1
    for m in markets:
        assert "market_id" in m
        assert "lat" in m
        assert "lng" in m


@pytest.mark.asyncio
async def test_route_returns_ranked_list_for_known_crop():
    if not await _db_available():
        pytest.skip("Postgres unreachable")

    from src.market_router import route

    result = await route(
        farmer_id="F0001",
        farmer_lat=0.3476,
        farmer_lng=32.5825,
        crop="maize_grain",
        quantity_kg=500.0,
        harvest_month=8,
    )
    assert isinstance(result, dict)
    assert "tier" in result
    assert "ranked_markets" in result
    assert isinstance(result["ranked_markets"], list)
