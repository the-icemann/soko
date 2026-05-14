"""
data-ingestion-service — entry point.

Modes of operation:
  Bootstrap  — on startup, pulls all profiles and historical orders from backend services.
               Only runs when the feature store tables are completely empty.
  Streaming  — runs continuously, consuming soko.transactions for live price observations.
  HTTP API   — exposes /health, /bootstrap, /ingest/* endpoints.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse

from .feature_store import get_pool, close_pool, is_bootstrap_needed, get_all_coverage, get_gap_summary
from .health import full_health_check
from .schemas import BootstrapStatusResponse, IngestOrderEventPayload
from .streams.transaction_stream import TransactionStream

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()

BOOTSTRAP_ON_STARTUP = os.getenv("BOOTSTRAP_ON_STARTUP", "true").lower() == "true"
SERVICE_NAME         = os.getenv("SERVICE_NAME", "data-ingestion-service")

_stream: TransactionStream | None = None
_bootstrap_lock = asyncio.Lock()


async def _run_bootstrap() -> dict:
    from .bootstrap.auth_bootstrap import bootstrap_farmers, bootstrap_buyers
    from .bootstrap.order_bootstrap import bootstrap_orders
    from .bootstrap.listing_bootstrap import bootstrap_listings, bootstrap_market_bootstrap

    await bootstrap_market_bootstrap()
    farmers  = await bootstrap_farmers()
    buyers   = await bootstrap_buyers()
    orders   = await bootstrap_orders()
    listings = await bootstrap_listings()
    return {"farmers": farmers, "buyers": buyers, "orders": orders, "listings": listings}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _stream

    # Initialise connection pool
    await get_pool()
    log.info("postgres_pool_ready")

    # Bootstrap if tables are empty and flag is set
    if BOOTSTRAP_ON_STARTUP:
        async with _bootstrap_lock:
            needed = await is_bootstrap_needed()
            if needed:
                log.info("bootstrap_starting")
                try:
                    result = await _run_bootstrap()
                    log.info("bootstrap_complete", **result)
                except Exception as exc:
                    log.error(f"bootstrap_failed: {exc}")
            else:
                log.info("bootstrap_skipped_tables_not_empty")

    # Start transaction stream consumer
    _stream = TransactionStream()
    _stream.start()
    log.info("transaction_stream_started")

    yield

    if _stream:
        _stream.stop()
    await close_pool()
    log.info("data_ingestion_service_stopped")


app = FastAPI(title="Soko Data Ingestion Service", version="2.0.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return await full_health_check()


@app.post("/bootstrap")
async def trigger_bootstrap(background_tasks: BackgroundTasks):
    """
    Manually triggers a full bootstrap regardless of table state.
    Used by `make ingest-bootstrap`.
    """
    async def _do_bootstrap():
        async with _bootstrap_lock:
            log.info("manual_bootstrap_triggered")
            result = await _run_bootstrap()
            log.info("manual_bootstrap_complete", **result)

    background_tasks.add_task(_do_bootstrap)
    return {"message": "Bootstrap triggered — running in background"}


@app.get("/bootstrap/status", response_model=BootstrapStatusResponse)
async def bootstrap_status():
    from .feature_store import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        farmers  = await conn.fetchval("SELECT COUNT(*) FROM farmer_features")
        buyers   = await conn.fetchval("SELECT COUNT(*) FROM buyer_features")
        orders   = await conn.fetchval(
            "SELECT COUNT(*) FROM price_observations WHERE source = 'soko_order'"
        )
        coverage = await conn.fetchval("SELECT COUNT(*) FROM coverage_map")

    return BootstrapStatusResponse(
        farmers_ingested=farmers,
        buyers_ingested=buyers,
        orders_ingested=orders,
        coverage_pairs=coverage,
        already_bootstrapped=(farmers > 0 or buyers > 0),
    )


@app.post("/ingest/order-event")
async def ingest_order_event(payload: IngestOrderEventPayload):
    """
    Accepts a transaction event forwarded by kafka-agent (or called directly).
    Processes as a price observation.
    """
    from .transformers.price_transformer import transform_transaction_event
    from .feature_store import insert_price_observation

    rec = transform_transaction_event(payload.model_dump())
    if rec is None:
        return {"status": "skipped", "reason": "not a purchase_completed or zero price"}

    inserted = await insert_price_observation(rec)
    return {"status": "inserted" if inserted else "rejected_outlier"}


@app.get("/gaps/summary")
async def gap_summary():
    return await get_gap_summary()


@app.get("/coverage")
async def coverage():
    return await get_all_coverage()
