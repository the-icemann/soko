from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.database import Base, engine
from app.routers import orders


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Soko Order Service",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/orders"
)

#app.include_router(internal.router, prefix="/internal")
app.include_router(orders.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "orders"}