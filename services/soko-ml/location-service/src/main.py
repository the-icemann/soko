"""
location-service — FastAPI entry point.
Exposes /health, /route, /discover.
"""
import logging
import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .cache import get_redis, close_redis
from .market_router import load_market_registry, close_pool as close_router_pool
from .fallback import close_pool as close_fallback_pool, _get_pool as get_fallback_pool
from .schemas import RouteRequest, RouteResponse, DiscoverRequest, DiscoverResponse

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()

SERVICE_NAME = os.getenv("SERVICE_NAME", "location-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_redis()
    await load_market_registry()
    log.info("location_service_started")
    yield
    await close_redis()
    await close_router_pool()
    await close_fallback_pool()
    log.info("location_service_stopped")


app = FastAPI(title="Soko Location Service", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    markets = await load_market_registry()
    return {
        "status":          "ok",
        "service":         SERVICE_NAME,
        "markets_loaded":  len(markets),
    }


@app.post("/route", response_model=RouteResponse)
async def route_endpoint(request: RouteRequest):
    from .cache import get_route, set_route
    from .market_router import route
    from .fallback import determine_tier, build_tier3_response, handle_unknown_crop

    # Check tier before computing
    # If the crop is completely unknown, go straight to Tier 3
    from .fallback import _find_category
    if not _find_category(request.crop):
        # Attempt to find it in coverage_map
        tier = await determine_tier(request.crop, "Kisenyi_Kampala")
        if tier == 3:
            await handle_unknown_crop(request.crop, request.farmer_id)
            result = build_tier3_response(request.farmer_id, request.crop, request.quantity_kg)
            return JSONResponse(content=result)

    # Check route cache
    cached = await get_route(request.farmer_id, request.crop, request.quantity_kg)
    if cached:
        return JSONResponse(content=cached)

    result = await route(
        farmer_id=request.farmer_id,
        farmer_lat=request.farmer_lat,
        farmer_lng=request.farmer_lng,
        crop=request.crop,
        quantity_kg=request.quantity_kg,
        max_distance_km=request.max_distance_km,
    )

    if result.get("tier") == 1 and result.get("ranked_markets"):
        await set_route(request.farmer_id, request.crop, request.quantity_kg, result)

    return JSONResponse(content=result)


@app.post("/discover", response_model=DiscoverResponse)
async def discover_endpoint(request: DiscoverRequest):
    from .cache import get_discover, set_discover
    from .geo_recommender import discover_farmers

    cached = await get_discover(request.buyer_id, request.crop, request.max_price_ugx)
    if cached:
        return JSONResponse(content=cached)

    results = await discover_farmers(
        buyer_lat=request.buyer_lat,
        buyer_lng=request.buyer_lng,
        crop=request.crop,
        max_price_ugx=request.max_price_ugx,
        max_distance_km=request.max_distance_km,
        top_n=request.top_n,
        buyer_district="",
    )

    response = {
        "buyer_id": request.buyer_id,
        "crop":     request.crop,
        "results":  results,
    }

    await set_discover(request.buyer_id, request.crop, request.max_price_ugx, response)
    return JSONResponse(content=response)
