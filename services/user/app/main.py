from fastapi import FastAPI
from contextlib import asynccontextmanager
from app.db.database import Base, engine
from app.routers import follows, internal, profile, reviews, settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(
    title="Soko User Service",
    description="User profiles, stats, reviews and follows",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/users",
)
@app.get("/health")
def health():
    return {"status": "ok"}

app.include_router(follows.router)
app.include_router(internal.router)
app.include_router(profile.router)
app.include_router(settings.router)
app.include_router(reviews.router)