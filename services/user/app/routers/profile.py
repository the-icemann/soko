from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import or_
from typing import Optional
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.core.dependencies import get_current_user_id
from app.models.user import UserProfile
from app.schemas.schemas import AuthenticatedUser, BuyerPublicProfile, FarmerProfile, UpdateProfile, UserRole
from app.helpers.builders import build_authenticated_user, build_farmer_profile, make_initials
import uuid

router = APIRouter(tags=["Profile"])


@router.get("/me", response_model=AuthenticatedUser)
def get_my_profile(
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    user = db.query(UserProfile).filter(UserProfile.id == uuid.UUID(user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Profile not found")
    return build_authenticated_user(user)


@router.put("/me", response_model=AuthenticatedUser)
def update_my_profile(
    payload: UpdateProfile,
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db)
):
    user = db.query(UserProfile).filter(UserProfile.id == uuid.UUID(user_id)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Profile not found")

    if payload.fullName    is not None: user.full_name  = payload.fullName
    if payload.phone       is not None: user.phone      = payload.phone
    if payload.district    is not None: user.district   = payload.district
    if payload.village     is not None: user.village    = payload.village
    if payload.avatarUrl   is not None: user.avatar_url = payload.avatarUrl
    if payload.farmerBio   is not None: user.farmer_bio = payload.farmerBio
    if payload.farmName    is not None: user.farm_name  = payload.farmName
    if payload.specialties is not None: user.specialties = ",".join(payload.specialties)
    if payload.interests   is not None: user.interests   = ",".join(payload.interests)

    db.commit()
    db.refresh(user)
    return build_authenticated_user(user)

@router.get("/farmers", response_model=list[FarmerProfile])
def get_farmers(
    district:  Optional[str]  = Query(default=None),
    verified:  Optional[bool] = Query(default=None),
    search:    Optional[str]  = Query(default=None),
    page:      int            = Query(default=1, ge=1),
    limit:     int            = Query(default=20, le=100),
    x_user_id: str            = Header(default=None),
    db: Session               = Depends(get_db)
):
    """
    Returns all users with role farmer or both.
    Public route — no auth required.
    """
    q = db.query(UserProfile).filter(
        UserProfile.role.in_([UserRole.farmer, UserRole.both])
    )

    if district:
        q = q.filter(UserProfile.district == district)

    if verified is not None:
        q = q.filter(UserProfile.verified == verified)

    if search:
        term = f"%{search}%"
        q = q.filter(
            or_(
                UserProfile.full_name.ilike(term),
                UserProfile.farm_name.ilike(term),
                UserProfile.farmer_bio.ilike(term),
            )
        )

    total   = q.count()
    farmers = q.order_by(UserProfile.created_at.desc()) \
               .offset((page - 1) * limit).limit(limit).all()

    return [
        build_farmer_profile(farmer, viewer_id=x_user_id, db=db)
        for farmer in farmers
    ]

@router.get("/buyers", response_model=list[AuthenticatedUser])
def get_buyers(
    district:  Optional[str]  = Query(default=None),
    page:      int            = Query(default=1, ge=1),
    limit:     int            = Query(default=100, le=500),
    db: Session               = Depends(get_db)
):
    """
    Returns all users with role buyer or both.
    Used by data-ingestion-service bootstrap to populate buyer_features table.
    """
    from app.helpers.builders import build_authenticated_user
    q = db.query(UserProfile).filter(
        UserProfile.role.in_([UserRole.buyer, UserRole.both])
    )
    if district:
        q = q.filter(UserProfile.district == district)

    buyers = q.order_by(UserProfile.created_at.desc()) \
               .offset((page - 1) * limit).limit(limit).all()
    return [build_authenticated_user(b) for b in buyers]


@router.get("/buyers/{user_id}", response_model=BuyerPublicProfile)
def get_buyer_profile(user_id: str, db: Session = Depends(get_db)):
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")

    user = db.query(UserProfile).filter(UserProfile.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role not in (UserRole.buyer, UserRole.both):
        raise HTTPException(status_code=404, detail="User is not a buyer")

    interests = [i.strip() for i in user.interests.split(",") if i.strip()] if user.interests else []
    bs = user.buyer_stats

    return BuyerPublicProfile(
        id=str(user.id),
        name=user.full_name or "",
        initials=make_initials(user.full_name or user.email),
        avatarUrl=user.avatar_url,
        district=user.district or "",
        verified=user.verified,
        interests=interests,
        memberSince=user.created_at.isoformat(),
        totalOrders=bs.total_orders if bs else None,
    )


@router.get("/{user_id}", response_model=FarmerProfile)
def get_farmer_profile(user_id: str, db: Session = Depends(get_db)):
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="User not found")

    user = db.query(UserProfile).filter(UserProfile.id == uid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role not in (UserRole.farmer, UserRole.both):
        raise HTTPException(status_code=404, detail="User not a farmer or both")
    return build_farmer_profile(user, viewer_id=user_id, db=db)