"""
Integration tests for the data-ingestion-service feature store.
Requires a live Postgres instance (set POSTGRES_DSN in environment).
Skipped automatically when the DB is unreachable.
"""
import os
import pytest
import pytest_asyncio
import asyncpg

pytestmark = pytest.mark.asyncio

DSN = os.getenv(
    "POSTGRES_DSN",
    "postgresql://soko_ml:changeme@localhost:5432/soko_ml_db",
)


async def _db_reachable() -> bool:
    try:
        conn = await asyncpg.connect(DSN, timeout=3)
        await conn.close()
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="module")
async def pool():
    if not await _db_reachable():
        pytest.skip("Postgres unreachable — skipping feature store integration tests")
    p = await asyncpg.create_pool(DSN, min_size=1, max_size=2)
    yield p
    await p.close()


# ── Farmer upsert ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_farmer_inserts_new_row(pool):
    from src.feature_store import upsert_farmer

    farmer = {
        "farmer_id": "TEST_F001",
        "name": "Test Farmer",
        "district": "Kampala",
        "lat": 0.3476,
        "lng": 32.5825,
        "crops_offered": ["maize_grain", "yellow_beans"],
        "avg_rating": 4.0,
        "fulfillment_rate": 0.95,
        "response_time_hours": 8.0,
        "total_sales": 0,
    }
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM farmer_features WHERE farmer_id = $1", farmer["farmer_id"])

    await upsert_farmer(farmer)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM farmer_features WHERE farmer_id = $1", farmer["farmer_id"]
        )
    assert row is not None
    assert row["name"] == "Test Farmer"
    assert "maize_grain" in row["crops_offered"]

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM farmer_features WHERE farmer_id = $1", farmer["farmer_id"])


@pytest.mark.asyncio
async def test_upsert_farmer_updates_existing_row(pool):
    from src.feature_store import upsert_farmer

    farmer = {
        "farmer_id": "TEST_F002",
        "name": "Initial Name",
        "district": "Gulu",
        "lat": 2.7747,
        "lng": 32.2990,
        "crops_offered": ["sorghum"],
        "avg_rating": 3.5,
        "fulfillment_rate": 0.80,
        "response_time_hours": 24.0,
        "total_sales": 0,
    }
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM farmer_features WHERE farmer_id = $1", farmer["farmer_id"])

    await upsert_farmer(farmer)
    farmer["name"] = "Updated Name"
    farmer["avg_rating"] = 4.8
    await upsert_farmer(farmer)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM farmer_features WHERE farmer_id = $1", farmer["farmer_id"]
        )
    assert row["name"] == "Updated Name"
    assert float(row["avg_rating"]) == pytest.approx(4.8, abs=0.01)

    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM farmer_features WHERE farmer_id = $1", farmer["farmer_id"])


# ── Outlier rejection ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insert_price_observation_accepts_normal_price(pool):
    from src.feature_store import insert_price_observation

    obs = {
        "order_id": "TEST-OBS-001",
        "crop": "maize_grain",
        "market": "Kisenyi_Kampala",
        "price_ugx_per_kg": 1400.0,
        "quantity_kg": 100.0,
        "observed_at": "2026-05-14T10:00:00+00:00",
        "source": "soko_order",
    }
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM price_observations WHERE order_id = $1", obs["order_id"]
        )

    accepted = await insert_price_observation(obs)
    assert accepted is True

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM price_observations WHERE order_id = $1", obs["order_id"]
        )
    assert row is not None
    assert float(row["price_ugx_per_kg"]) == pytest.approx(1400.0)

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM price_observations WHERE order_id = $1", obs["order_id"]
        )


@pytest.mark.asyncio
async def test_insert_price_observation_rejects_outlier(pool):
    """
    Seeds enough observations to establish a mean, then tries a 10σ outlier.
    """
    from src.feature_store import insert_price_observation, bulk_insert_price_observations

    seed_obs = [
        {
            "order_id": f"SEED-OBS-{i:03d}",
            "crop": "sorghum",
            "market": "Gulu",
            "price_ugx_per_kg": 900.0 + i,
            "quantity_kg": 50.0,
            "observed_at": f"2026-04-{i+1:02d}T10:00:00+00:00",
            "source": "soko_order",
        }
        for i in range(30)
    ]
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM price_observations WHERE order_id LIKE 'SEED-OBS-%'"
        )

    await bulk_insert_price_observations(seed_obs)

    outlier = {
        "order_id": "OUTLIER-OBS-001",
        "crop": "sorghum",
        "market": "Gulu",
        "price_ugx_per_kg": 999999.0,    # obviously anomalous
        "quantity_kg": 50.0,
        "observed_at": "2026-05-14T11:00:00+00:00",
        "source": "soko_order",
    }
    accepted = await insert_price_observation(outlier)
    assert accepted is False

    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM price_observations WHERE order_id LIKE 'SEED-OBS-%' OR order_id = 'OUTLIER-OBS-001'"
        )


# ── Coverage map ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_coverage_status_returns_dict(pool):
    from src.feature_store import get_coverage_status

    status = await get_coverage_status("maize_grain", "Kisenyi_Kampala")
    # May return None if the seed data is not yet present; just assert no crash
    assert status is None or isinstance(status, dict)


@pytest.mark.asyncio
async def test_get_all_coverage_returns_list(pool):
    from src.feature_store import get_all_coverage

    coverage = await get_all_coverage()
    assert isinstance(coverage, list)
