from pydantic import BaseModel, EmailStr, field_validator
from typing import List, Optional
from enum import Enum


class UserRole(str, Enum):
    buyer  = "buyer"
    farmer = "farmer"
    both   = "both"
    admin  = "admin"


class VerificationStatus(str, Enum):
    unverified = "unverified"
    pending    = "pending"
    verified   = "verified"
    rejected   = "rejected"


# ── Called by Auth Service on register (internal)
class CreateUserPayload(BaseModel):
    id:           str
    email:        EmailStr
    role:         UserRole
    full_name:    str
    phone:        str
    district:     str
    avatar_url:   Optional[str] = None
    specialties:  Optional[List[str]] = None
    interests:    Optional[List[str]] = None


# ── AuthenticatedUser — full private profile (GET /users/me)
class AuthenticatedUser(BaseModel):
    id:                 str
    name:               str
    initials:           str
    email:              str
    phone:              str
    avatarUrl:          Optional[str]
    district:           str
    village:            Optional[str]
    role:               UserRole
    verified:           bool
    verificationStatus: VerificationStatus
    memberSince:        str
    # Farmer-specific
    farmerBio:          Optional[str]
    farmName:           Optional[str]
    specialties:        List[str]
    interests:          List[str]
    # Buyer stats
    totalOrders:        Optional[int]
    totalSpent:         Optional[int]
    wishlistCount:      Optional[int]
    # Farmer stats
    totalListings:      Optional[int]
    totalSales:         Optional[int]
    totalEarned:        Optional[int]
    pendingPayout:      Optional[int]
    averageRating:      Optional[float]
    totalReviews:       Optional[int]


# ── FarmerProfile — public view (GET /users/{id})
class FarmerProfile(BaseModel):
    id:             str
    name:           str
    initials:       str
    avatarUrl:      Optional[str]
    district:       str
    village:        Optional[str]
    verified:       bool
    farmerBio:      Optional[str]
    farmName:       Optional[str]
    specialties:    List[str]        
    memberSince:    str
    totalListings:  int
    totalSales:     int
    averageRating:  float
    totalReviews:   int
    responseTime:   Optional[str]
    isFollowedByMe: Optional[bool] = None
    isRatedByMe:    Optional[int]  = None

# ── BuyerPublicProfile — public view (GET /users/buyers/{id})
class BuyerPublicProfile(BaseModel):
    id:          str
    name:        str
    initials:    str
    avatarUrl:   Optional[str]
    district:    str
    verified:    bool
    interests:   List[str]
    memberSince: str
    totalOrders: Optional[int]

# ── FarmerReview
class FarmerReviewOut(BaseModel):
    id:               str
    reviewerId:       str
    reviewerName:     str
    reviewerInitials: str
    rating:           int
    body:             str
    createdAt:        str
    helpful:          int
    isHelpfulByMe:    Optional[bool] = None


class CreateReviewPayload(BaseModel):
    rating: int
    body:   str

    @field_validator("rating")
    @classmethod
    def valid_rating(cls, v):
        if not 1 <= v <= 5:
            raise ValueError("Rating must be between 1 and 5")
        return v


# ── Profile update
class UpdateProfile(BaseModel):
    fullName:    Optional[str]       = None
    phone:       Optional[str]       = None
    district:    Optional[str]       = None
    village:     Optional[str]       = None
    avatarUrl:   Optional[str]       = None
    farmerBio:   Optional[str]       = None
    farmName:    Optional[str]       = None
    specialties: Optional[List[str]] = None
    interests:   Optional[List[str]] = None

    @field_validator("specialties")
    @classmethod
    def max_specialties(cls, v):
        if v and len(v) > 3:
            raise ValueError("Maximum 3 specialties allowed")
        return v

    @field_validator("interests")
    @classmethod
    def max_interests(cls, v):
        if v and len(v) > 3:
            raise ValueError("Maximum 3 interests allowed")
        return v


# ── Settings
class UserSettingsOut(BaseModel):
    theme:               str
    notificationsEmail:  bool
    notificationsSms:    bool
    notificationsPush:   bool
    language:            str
    currency:            str


class UpdateSettings(BaseModel):
    theme:               Optional[str]  = None
    notificationsEmail:  Optional[bool] = None
    notificationsSms:    Optional[bool] = None
    notificationsPush:   Optional[bool] = None
    language:            Optional[str]  = None   # en | lg | sw
    currency:            Optional[str]  = None


# ── Internal stats update — called by Order Service
class UpdateFarmerStats(BaseModel):
    total_listings: Optional[int]   = None
    total_sales:    Optional[int]   = None
    total_earned:   Optional[int]   = None
    pending_payout: Optional[int]   = None
    average_rating: Optional[float] = None
    total_reviews:  Optional[int]   = None
    response_time:  Optional[str]   = None


class UpdateBuyerStats(BaseModel):
    total_orders:   Optional[int] = None
    total_spent:    Optional[int] = None
    wishlist_count: Optional[int] = None