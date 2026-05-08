import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.database import Base, engine
from app.routers import payments, webhook, internal

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    # Register IPN URL with PesaPal on startup
    try:
        from app.routers.internal import get_ipn_id
        ipn_id = await get_ipn_id()
        logger.info(f"PesaPal IPN registered: {ipn_id}")
    except Exception as e:
        logger.warning(f"PesaPal IPN registration failed on startup: {e}")
    yield


app = FastAPI(
    title="Soko Payment Service",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/payments"
)

app.include_router(internal.router, prefix="/internal")
app.include_router(webhook.router,  prefix="/webhook")
app.include_router(payments.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "payments"}