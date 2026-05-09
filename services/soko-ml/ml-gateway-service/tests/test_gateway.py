import os
import sys
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["PRICE_SERVICE_URL"] = "http://mock-price:8001"
os.environ["REC_SERVICE_URL"] = "http://mock-rec:8002"

from src.main import app
from src.proxy import _breakers, CircuitState, CircuitBreaker


@pytest.fixture(autouse=True)
def reset_breakers():
    for b in _breakers.values():
        b._state = CircuitState.CLOSED
        b._failure_count = 0
        b._last_failure_time = None


# ── Price proxy tests ─────────────────────────────────────────────────────────

@respx.mock
def test_price_predict_proxy_success():
    respx.post("http://mock-price:8001/predict").mock(
        return_value=httpx.Response(200, json={
            "market": "Kisenyi_Kampala", "crop": "maize_grain",
            "currency": "UGX", "price_type": "wholesale", "cached": False,
            "predictions": [{"date": "2025-06-02", "predicted_price_ugx": 1312, "lower_bound": 1180, "upper_bound": 1445}],
        })
    )
    with TestClient(app) as client:
        resp = client.post(
            "/price/predict",
            json={"market": "Kisenyi_Kampala", "crop": "maize_grain", "weeks_ahead": 1},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["market"] == "Kisenyi_Kampala"
    assert data["currency"] == "UGX"
    assert len(data["predictions"]) == 1


@respx.mock
def test_price_predict_graceful_fallback_on_connection_error():
    respx.post("http://mock-price:8001/predict").mock(side_effect=httpx.ConnectError("refused"))
    with TestClient(app) as client:
        resp = client.post(
            "/price/predict",
            json={"market": "Gulu", "crop": "sorghum", "weeks_ahead": 4},
        )
    assert resp.status_code == 503
    data = resp.json()
    assert "predictions" in data
    assert "error" in data


@respx.mock
def test_price_markets_proxy():
    respx.get("http://mock-price:8001/markets").mock(
        return_value=httpx.Response(200, json={"markets": ["Kisenyi_Kampala", "Gulu"]})
    )
    with TestClient(app) as client:
        resp = client.get("/price/markets")
    assert resp.status_code == 200
    assert "markets" in resp.json()


# ── Recommendation proxy tests ────────────────────────────────────────────────

@respx.mock
def test_recommend_farmers_proxy_success():
    respx.get("http://mock-rec:8002/recommend/farmers-for-buyer/B0001").mock(
        return_value=httpx.Response(200, json={
            "buyer_id": "B0001", "cached": False, "recommended_farmers": [],
        })
    )
    with TestClient(app) as client:
        resp = client.get("/recommend/farmers-for-buyer/B0001")
    assert resp.status_code == 200
    assert resp.json()["buyer_id"] == "B0001"


@respx.mock
def test_recommend_buyers_proxy_success():
    respx.get("http://mock-rec:8002/recommend/buyers-for-farmer/F0001").mock(
        return_value=httpx.Response(200, json={
            "farmer_id": "F0001", "cached": False, "recommended_buyers": [],
        })
    )
    with TestClient(app) as client:
        resp = client.get("/recommend/buyers-for-farmer/F0001")
    assert resp.status_code == 200


# ── Health tests ──────────────────────────────────────────────────────────────

@respx.mock
def test_health_all_ok():
    respx.get("http://mock-price:8001/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    respx.get("http://mock-rec:8002/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["gateway"] == "ok"
    assert data["services"]["price-prediction"] == "ok"
    assert data["services"]["recommendation"] == "ok"


@respx.mock
def test_health_degraded_when_price_down():
    respx.get("http://mock-price:8001/health").mock(side_effect=httpx.ConnectError("down"))
    respx.get("http://mock-rec:8002/health").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    with TestClient(app) as client:
        resp = client.get("/health")
    data = resp.json()
    assert data["gateway"] == "degraded"
    assert data["services"]["price-prediction"] == "unreachable"


# ── Circuit breaker unit tests ────────────────────────────────────────────────

def test_circuit_breaker_opens_after_threshold():
    b = CircuitBreaker("test")
    for _ in range(3):
        b.record_failure()
    assert b.is_open()


def test_circuit_breaker_closed_after_success():
    b = CircuitBreaker("test")
    for _ in range(3):
        b.record_failure()
    b.record_success()
    assert not b.is_open()
    assert b._failure_count == 0


def test_circuit_breaker_initial_state_closed():
    b = CircuitBreaker("test")
    assert not b.is_open()
    assert b.state == CircuitState.CLOSED


def test_circuit_breaker_two_failures_not_open():
    b = CircuitBreaker("test")
    b.record_failure()
    b.record_failure()
    assert not b.is_open()
