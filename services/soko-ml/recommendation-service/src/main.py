import asyncio
import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Query

from .cache import (
    get_redis_client,
    get_cached_farmers, set_cached_farmers,
    get_cached_buyers, set_cached_buyers,
)
from .feature_store_client import close_pool
from .interaction_store import InteractionStore
from .kafka_consumer import InteractionConsumer
from .recommender import ProfileStore, Recommender
from .schemas import (
    FarmersForBuyerResponse, BuyersForFarmerResponse,
    FarmerRecommendation, BuyerRecommendation, HealthResponse,
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()

DEFAULT_TOP_N              = int(os.getenv("DEFAULT_TOP_N", "5"))
SERVICE_NAME               = os.getenv("SERVICE_NAME", "recommendation-service")
PROFILE_REFRESH_INTERVAL   = int(os.getenv("PROFILE_REFRESH_INTERVAL_SECONDS", "900"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    profile_store = ProfileStore()

    # Initial load — exit if Postgres is unreachable (service cannot function without profiles)
    try:
        n_farmers, n_buyers = await profile_store.reload()
    except Exception as exc:
        log.critical(f"Cannot load profiles from feature store at startup: {exc}")
        raise SystemExit(1)

    interaction_store = InteractionStore()
    redis_client      = await get_redis_client()
    recommender       = Recommender(profile_store, interaction_store)

    consumer = InteractionConsumer(interaction_store)
    consumer.start()

    app.state.recommender   = recommender
    app.state.redis         = redis_client
    app.state.profile_store = profile_store
    app.state.consumer      = consumer

    log.info("recommendation_service_started", farmers=n_farmers, buyers=n_buyers)

    # Periodic reload task
    async def _reload_loop():
        while True:
            await asyncio.sleep(PROFILE_REFRESH_INTERVAL)
            try:
                await profile_store.reload()
            except Exception as exc:
                log.warning(f"Periodic profile reload failed: {exc}")

    reload_task = asyncio.create_task(_reload_loop())

    yield

    reload_task.cancel()
    consumer.stop()
    await redis_client.aclose()
    await close_pool()
    log.info("recommendation_service_stopped")


app = FastAPI(title="Soko Recommendation Service", version="1.0.0", lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health():
    ps: ProfileStore = app.state.profile_store
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        farmers_loaded=len(ps.farmers),
        buyers_loaded=len(ps.buyers),
    )


@app.get("/recommend/farmers-for-buyer/{buyer_id}", response_model=FarmersForBuyerResponse)
async def farmers_for_buyer(
    buyer_id: str, top_n: int = Query(default=DEFAULT_TOP_N, ge=1, le=50)
):
    redis = app.state.redis
    recommender: Recommender = app.state.recommender

    cached = await get_cached_farmers(redis, buyer_id, top_n)
    if cached is not None:
        return FarmersForBuyerResponse(
            buyer_id=buyer_id,
            cached=True,
            recommended_farmers=[FarmerRecommendation(**f) for f in cached],
        )

    recommendations = recommender.recommend_farmers_for_buyer(buyer_id, top_n)
    if not recommendations and recommender.profiles.get_buyer(buyer_id) is None:
        raise HTTPException(status_code=404, detail=f"Buyer {buyer_id} not found")

    await set_cached_farmers(redis, buyer_id, top_n, recommendations)
    log.info("farmers_recommended", buyer_id=buyer_id, count=len(recommendations))

    return FarmersForBuyerResponse(
        buyer_id=buyer_id,
        cached=False,
        recommended_farmers=[FarmerRecommendation(**f) for f in recommendations],
    )


@app.get("/recommend/buyers-for-farmer/{farmer_id}", response_model=BuyersForFarmerResponse)
async def buyers_for_farmer(
    farmer_id: str, top_n: int = Query(default=DEFAULT_TOP_N, ge=1, le=50)
):
    redis = app.state.redis
    recommender: Recommender = app.state.recommender

    cached = await get_cached_buyers(redis, farmer_id, top_n)
    if cached is not None:
        return BuyersForFarmerResponse(
            farmer_id=farmer_id,
            cached=True,
            recommended_buyers=[BuyerRecommendation(**b) for b in cached],
        )

    recommendations = recommender.recommend_buyers_for_farmer(farmer_id, top_n)
    if not recommendations and recommender.profiles.get_farmer(farmer_id) is None:
        raise HTTPException(status_code=404, detail=f"Farmer {farmer_id} not found")

    await set_cached_buyers(redis, farmer_id, top_n, recommendations)
    log.info("buyers_recommended", farmer_id=farmer_id, count=len(recommendations))

    return BuyersForFarmerResponse(
        farmer_id=farmer_id,
        cached=False,
        recommended_buyers=[BuyerRecommendation(**b) for b in recommendations],
    )
