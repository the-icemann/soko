from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.database import Base, engine
from app.routers import posts, comments


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Soko Blog Service",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/posts",
)

app.include_router(posts.router)
app.include_router(comments.router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "blog"}