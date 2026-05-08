from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.database import Base, engine
from app.routers import notifications, internal


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Soko Notification Service",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/notifications"
)

app.include_router(internal.router,      prefix="/internal")
app.include_router(notifications.router, prefix="/notifications")


@app.get("/health")
def health():
    return {"status": "ok", "service": "notifications"}