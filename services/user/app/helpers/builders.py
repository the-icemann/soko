from sqlalchemy.orm import Session
from app.models.user import UserProfile, FarmerFollow, FarmerReview
from app.schemas.schemas import AuthenticatedUser, FarmerProfile
import uuid


def make_initials(name: str) -> str:
    parts = name.strip().split()
    return (parts[0][0] + parts[-1][0]).upper() if len(parts) >= 2 else name[:2].upper()


def build_authenticated_user(user: UserProfile) -> AuthenticatedUser:
    fs = user.farmer_stats
    bs = user.buyer_stats
    specialties = [s.strip() for s in user.specialties.split(",") if s.strip()] if user.specialties else []
    interests   = [i.strip() for i in user.interests.split(",")   if i.strip()] if user.interests   else []
    return AuthenticatedUser(
        id=str(user.id),
        name=user.full_name or "",
        initials=make_initials(user.full_name or user.email),
        email=user.email,
        phone=user.phone or "",
        avatarUrl=user.avatar_url,
        district=user.district or "",
        village=user.village,
        role=user.role.value,
        verified=user.verified,
        verificationStatus=user.verification_status.value,
        memberSince=user.created_at.isoformat(),
        farmerBio=user.farmer_bio,
        farmName=user.farm_name,
        specialties=specialties,
        interests=interests,
        totalOrders=bs.total_orders     if bs else None,
        totalSpent=bs.total_spent       if bs else None,
        wishlistCount=bs.wishlist_count if bs else None,
        totalListings=fs.total_listings if fs else None,
        totalSales=fs.total_sales       if fs else None,
        totalEarned=fs.total_earned     if fs else None,
        pendingPayout=fs.pending_payout if fs else None,
        averageRating=fs.average_rating if fs else None,
        totalReviews=fs.total_reviews   if fs else None,
    )


def build_farmer_profile(
    user: UserProfile,
    viewer_id: str | None = None,
    db: Session = None
) -> FarmerProfile:
    fs          = user.farmer_stats
    is_followed = None
    is_rated    = None

    if viewer_id and db:
        is_followed = db.query(FarmerFollow).filter(
            FarmerFollow.farmer_id   == user.id,
            FarmerFollow.follower_id == uuid.UUID(viewer_id)
        ).first() is not None

        existing = db.query(FarmerReview).filter(
            FarmerReview.farmer_id   == user.id,
            FarmerReview.reviewer_id == uuid.UUID(viewer_id)
        ).first()
        is_rated = existing.rating if existing else None

    specialties = (
        [s.strip() for s in user.specialties.split(",") if s.strip()]
        if user.specialties else []
    )

    return FarmerProfile(
        id=str(user.id),
        name=user.full_name or "",
        initials=make_initials(user.full_name or user.email),
        avatarUrl=user.avatar_url,
        district=user.district or "",
        village=user.village,
        verified=user.verified,
        farmerBio=user.farmer_bio,
        farmName=user.farm_name,
        specialties=specialties,             
        memberSince=user.created_at.isoformat(),
        totalListings=fs.total_listings if fs else 0,
        totalSales=fs.total_sales       if fs else 0,
        averageRating=fs.average_rating if fs else 0.0,
        totalReviews=fs.total_reviews   if fs else 0,
        responseTime=fs.response_time   if fs else None,
        isFollowedByMe=is_followed,
        isRatedByMe=is_rated,
    )