import asyncio
import logging
from sqlite3 import IntegrityError

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import internal_only
from app.db.database import get_db
from app.models.user import UserProfile, FarmerStats, BuyerStats, UserSettings
from app.schemas.schemas import CreateUserPayload, UpdateFarmerStats, UpdateBuyerStats
import uuid

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Internal"], dependencies=[Depends(internal_only)])


async def _notify(client: httpx.AsyncClient, target_id: str, is_farmer: bool, message: str) -> None:
    key = "farmer_id" if is_farmer else "buyer_id"
    await client.post(
        f"{settings.NOTIFICATION_SERVICE_URL}/internal/notify",
        json={"event": "system", key: target_id, "meta": {"message": message}},
        headers={"x-internal-secret": settings.INTERNAL_SECRET},
    )


async def _post_signup_sync(
    user_id:     str,
    role:        str,
    full_name:   str,
    district:    str | None,
    specialties: list[str] | None,
    interests:   list[str] | None,
) -> None:
    """
    Fire-and-forget: syncs the new user to the ML feature store, reloads
    the recommendation service, then sends real-time notifications to
    matched counterparts in both directions.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # ── 1. Sync to ML feature store ────────────────────────────────────
            await client.post(
                f"{settings.INGEST_SERVICE_URL}/ingest/user-created",
                json={
                    "id":          user_id,
                    "role":        role,
                    "full_name":   full_name,
                    "district":    district,
                    "specialties": specialties or [],
                    "interests":   interests   or [],
                },
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
            )

            await asyncio.sleep(4)  # allow recommendation service to reload profiles

            location = district or "Uganda"

            # ── 2. New BUYER → notify matched farmers ──────────────────────────
            if role in ("buyer", "both") and interests:
                crops = ", ".join(interests[:2])
                rec = await client.get(
                    f"{settings.REC_SERVICE_URL}/recommend/farmers-for-buyer/{user_id}",
                    params={"top_n": 5},
                )
                if rec.status_code == 200:
                    for farmer in rec.json().get("recommended_farmers", []):
                        if farmer.get("matchScore", 0) < 0.15:
                            continue
                        await _notify(
                            client, farmer["id"], is_farmer=True,
                            message=(
                                f"{full_name} from {location} is looking for {crops}. "
                                f"Their interests match your produce — check them out on your home feed."
                            ),
                        )

            # ── 3. New FARMER → notify matched buyers ──────────────────────────
            if role in ("farmer", "both") and specialties:
                crops = ", ".join(specialties[:2])
                rec = await client.get(
                    f"{settings.REC_SERVICE_URL}/recommend/buyers-for-farmer/{user_id}",
                    params={"top_n": 5},
                )
                if rec.status_code == 200:
                    for buyer in rec.json().get("recommended_buyers", []):
                        if buyer.get("matchScore", 0) < 0.15:
                            continue
                        await _notify(
                            client, buyer["id"], is_farmer=False,
                            message=(
                                f"{full_name} from {location} just listed {crops}. "
                                f"They match your interests — see their farm on your home feed."
                            ),
                        )

    except Exception as exc:
        logger.warning(f"Post-signup sync failed for {user_id}: {exc}")



@router.post("/", status_code=201)
def create_user(
    payload:          CreateUserPayload,
    background_tasks: BackgroundTasks,
    db:               Session = Depends(get_db),
):
    """Auth Service calls this after creating credentials."""
    profile = UserProfile(
        id=uuid.UUID(payload.id),
        email=payload.email,
        role=payload.role,
        full_name=payload.full_name,
        phone=payload.phone,
        district=payload.district,
        avatar_url=payload.avatar_url,
        specialties=",".join(payload.specialties) if payload.specialties else None,
        interests=",".join(payload.interests)     if payload.interests    else None,
    )
    db.add(profile)
    if payload.role in ("farmer", "both"): db.add(FarmerStats(user_id=profile.id))
    if payload.role in ("buyer",  "both"): db.add(BuyerStats(user_id=profile.id))
    db.add(UserSettings(user_id=profile.id))

    try:
        db.commit()
        db.refresh(profile)
    except IntegrityError as e:
        db.rollback()
        detail = str(e.orig)
        if "phone" in detail:
            raise HTTPException(status_code=409, detail="Phone number already registered")
        if "email" in detail:
            raise HTTPException(status_code=409, detail="Email already registered")
        raise HTTPException(status_code=409, detail="User already exists")

    background_tasks.add_task(
        _post_signup_sync,
        user_id=str(profile.id),
        role=payload.role,
        full_name=payload.full_name,
        district=payload.district,
        specialties=payload.specialties,
        interests=payload.interests,
    )

    return {"id": str(profile.id)}


@router.put("/{user_id}/stats/farmer")
def update_farmer_stats(
    user_id: str,
    payload: UpdateFarmerStats,
    db: Session = Depends(get_db)
):
    fs = db.query(FarmerStats).filter(FarmerStats.user_id == uuid.UUID(user_id)).first()
    if not fs:
        raise HTTPException(status_code=404, detail="Farmer stats not found")

    if payload.total_listings is not None: fs.total_listings = payload.total_listings
    if payload.total_sales    is not None: fs.total_sales    = payload.total_sales
    if payload.total_earned   is not None: fs.total_earned   = payload.total_earned
    if payload.pending_payout is not None: fs.pending_payout = payload.pending_payout
    if payload.average_rating is not None: fs.average_rating = payload.average_rating
    if payload.total_reviews  is not None: fs.total_reviews  = payload.total_reviews
    if payload.response_time  is not None: fs.response_time  = payload.response_time

    db.commit()
    return {"updated": True}


@router.put("/{user_id}/stats/buyer")
def update_buyer_stats(
    user_id: str,
    payload: UpdateBuyerStats,
    db: Session = Depends(get_db)
):
    bs = db.query(BuyerStats).filter(BuyerStats.user_id == uuid.UUID(user_id)).first()
    if not bs:
        raise HTTPException(status_code=404, detail="Buyer stats not found")

    if payload.total_orders   is not None: bs.total_orders   = payload.total_orders
    if payload.total_spent    is not None: bs.total_spent    = payload.total_spent
    if payload.wishlist_count is not None: bs.wishlist_count = payload.wishlist_count

    db.commit()
    return {"updated": True}

@router.get("/user/{user_id}")
def get_user_for_service(user_id: str, db: Session = Depends(get_db)):
    """
    Called by Payment Service and Produce Service to fetch
    name, email, phone for a user of any role.
    Protected by internal_only.
    """
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    user = db.query(UserProfile).filter(UserProfile.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "name":     user.name,
        "email":    user.email,
        "phone":    user.phone,
        "district": user.district,
        "verified": user.verified,
        "role":     user.role.value,
    }