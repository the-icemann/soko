from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from app.db.database import Base, engine
from app.routers.ussd import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Soko USSD Service",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/ussd",
    default_response_class=PlainTextResponse,
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "ussd"}