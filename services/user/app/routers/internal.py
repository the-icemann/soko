from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.core.dependencies import internal_only
from app.models.user import UserProfile, FarmerStats, BuyerStats, UserSettings
from app.schemas.schemas import CreateUserPayload, UpdateFarmerStats, UpdateBuyerStats
import uuid

router = APIRouter(tags=["Internal"], dependencies=[Depends(internal_only)])


@router.post("/", status_code=201)
def create_user(payload: CreateUserPayload, db: Session = Depends(get_db)):
    """ Auth Service calls this after creating credentials. """
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

    db.commit()
    db.refresh(profile)
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