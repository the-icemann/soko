import logging
from typing import List

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.security import decode_token
from fastapi.security import OAuth2PasswordBearer

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alert", tags=["alerts"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

DEVELOPER_EMAIL = "andrewssuubi@gmail.com"
SENDGRID_URL = "https://api.sendgrid.com/v3/mail/send"


class UnsupportedCropPayload(BaseModel):
    crops: List[str]
    user_id: str


@router.post("/unsupported-crop", status_code=204)
async def unsupported_crop_alert(
    payload: UnsupportedCropPayload,
    token: str = Depends(oauth2_scheme),
):
    if not token or not decode_token(token):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not settings.SENDGRID_API_KEY or not settings.SENDGRID_FROM_EMAIL:
        logger.warning("SendGrid not configured — skipping developer alert")
        return

    crop_list = ", ".join(payload.crops)
    body = {
        "personalizations": [{"to": [{"email": DEVELOPER_EMAIL}]}],
        "from": {"email": settings.SENDGRID_FROM_EMAIL},
        "subject": f"[Soko] Unsupported crop request: {crop_list}",
        "content": [
            {
                "type": "text/plain",
                "value": (
                    f"A farmer (user_id: {payload.user_id}) has specialties/listings "
                    f"that fall outside the current ML crop coverage:\n\n"
                    f"  Crops: {crop_list}\n\n"
                    f"Consider adding a price model for these crops.\n\n"
                    f"-- Soko automated alert"
                ),
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                SENDGRID_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {settings.SENDGRID_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
            if resp.status_code not in (200, 202):
                logger.warning(f"SendGrid returned {resp.status_code}: {resp.text}")
    except Exception as exc:
        logger.error(f"Developer alert email failed: {exc}")
