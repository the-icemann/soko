from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.database import Base, engine
from app.routers import listings, images, reviews, internal


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Soko Produce Service",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/listings"
)

app.include_router(internal.router, prefix="/internal")
app.include_router(images.router)
app.include_router(reviews.router)
app.include_router(listings.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "produce"}