from fastapi import FastAPI
from contextlib import asynccontextmanager
from starlette.middleware.sessions import SessionMiddleware
from app.db.session import Base, engine
from app.core.config import settings
from app.routers import auth, oauth, bot_auth

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(
    title="Soko Auth Service",
    description="Authentication & identity for Soko Agrimarket",
    version="1.0.0",
    lifespan=lifespan,
    root_path="/auth",
)

app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY)  # ← must be here

app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(bot_auth.router)