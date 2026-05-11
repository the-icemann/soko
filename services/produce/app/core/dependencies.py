from fastapi import Header, HTTPException
from app.core.config import settings


def get_current_user_id(x_user_id: str = Header(default="")) -> str:
    return x_user_id


def get_current_user_role(x_user_role: str = Header(...)) -> str:
    return x_user_role


def farmer_only(x_user_role: str = Header(default="")):
    if x_user_role not in ("farmer", "both"):
        raise HTTPException(status_code=403, detail="Only farmers can perform this action")


def internal_only(x_internal_secret: str = Header(...)):
    if x_internal_secret != settings.INTERNAL_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")