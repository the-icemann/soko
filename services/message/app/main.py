from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.db.database import Base, engine
from app.routers import messages, ws,conversations


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Soko Messaging Service",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/message",

)

app.include_router(ws.router)
app.include_router(conversations.router, prefix="/conversations")
app.include_router(messages.router,prefix="/conversations")


@app.get("/health")
def health():
    return {"status": "ok", "service": "messaging_service"}