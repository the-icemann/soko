from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
import redis as redis_lib

from app.core.config import settings
from app.core.security import decode_token

router = APIRouter(prefix="/bot", tags=["bot-auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

_redis_client: redis_lib.Redis | None = None


def get_redis() -> redis_lib.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


class BotLinkPayload(BaseModel):
    botpress_user_id: str


@router.post("/link", status_code=204)
async def link_bot_user(
    payload: BotLinkPayload,
    token: str = Depends(oauth2_scheme),
):
    """
    Called by the frontend when the Botpress webchat connects.
    Validates the Soko JWT then stores botpressUserId → token in Redis (8h TTL).
    """
    if not decode_token(token):
        raise HTTPException(status_code=401, detail="Invalid token")
    get_redis().setex(f"bot:token:{payload.botpress_user_id}", 3600 * 8, token)


@router.get("/token/{botpress_user_id}")
async def get_bot_token(
    botpress_user_id: str,
    x_bot_secret: str = Header(...),
):
    """
    Called by the SokoBot on Botpress Cloud to retrieve the Soko JWT for a user.
    Protected by a shared secret — never exposed to the browser.
    """
    if not settings.BOT_SECRET or x_bot_secret != settings.BOT_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")
    token = get_redis().get(f"bot:token:{botpress_user_id}")
    if not token:
        raise HTTPException(status_code=404, detail="No token for this user")
    return {"token": token}
