import os
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from .logger import RequestLoggingMiddleware
from .proxy import proxy_request, get_service_breaker_status, PRICE_SERVICE_URL, REC_SERVICE_URL

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()

SERVICE_NAME = os.getenv("SERVICE_NAME", "ml-gateway-service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with httpx.AsyncClient() as client:
        app.state.http_client = client
        log.info("gateway_started", price_url=PRICE_SERVICE_URL, rec_url=REC_SERVICE_URL)
        yield
    log.info("gateway_stopped")


app = FastAPI(title="Soko ML Gateway", version="1.0.0", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)


@app.get("/health")
async def health():
    client: httpx.AsyncClient = app.state.http_client

    async def check_service(base_url: str) -> str:
        try:
            resp = await client.get(f"{base_url}/health", timeout=3.0)
            return "ok" if resp.status_code == 200 else "degraded"
        except Exception:
            return "unreachable"

    price_status = await check_service(PRICE_SERVICE_URL)
    rec_status = await check_service(REC_SERVICE_URL)
    overall = "ok" if price_status == "ok" and rec_status == "ok" else "degraded"

    return {
        "gateway": overall,
        "services": {
            "price-prediction": price_status,
            "recommendation": rec_status,
        },
        "circuit_breakers": get_service_breaker_status(),
    }


@app.post("/price/predict")
async def price_predict(request: Request):
    body = await request.json()
    client: httpx.AsyncClient = app.state.http_client
    result, status = await proxy_request(client, "POST", f"{PRICE_SERVICE_URL}/predict", json_body=body)
    return JSONResponse(content=result, status_code=status)


@app.get("/price/markets")
async def price_markets():
    client: httpx.AsyncClient = app.state.http_client
    result, status = await proxy_request(client, "GET", f"{PRICE_SERVICE_URL}/markets")
    return JSONResponse(content=result, status_code=status)


@app.get("/price/crops")
async def price_crops():
    client: httpx.AsyncClient = app.state.http_client
    result, status = await proxy_request(client, "GET", f"{PRICE_SERVICE_URL}/crops")
    return JSONResponse(content=result, status_code=status)


@app.get("/recommend/farmers-for-buyer/{buyer_id}")
async def recommend_farmers(buyer_id: str, top_n: int = Query(default=5, ge=1, le=50)):
    client: httpx.AsyncClient = app.state.http_client
    url = f"{REC_SERVICE_URL}/recommend/farmers-for-buyer/{buyer_id}"
    result, status = await proxy_request(client, "GET", url, params={"top_n": top_n})
    return JSONResponse(content=result, status_code=status)


@app.get("/recommend/buyers-for-farmer/{farmer_id}")
async def recommend_buyers(farmer_id: str, top_n: int = Query(default=5, ge=1, le=50)):
    client: httpx.AsyncClient = app.state.http_client
    url = f"{REC_SERVICE_URL}/recommend/buyers-for-farmer/{farmer_id}"
    result, status = await proxy_request(client, "GET", url, params={"top_n": top_n})
    return JSONResponse(content=result, status_code=status)
