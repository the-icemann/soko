from pydantic import BaseModel
from typing import List, Optional


class FarmerRecommendation(BaseModel):
    """
    Mirrors the user service FarmerProfile public schema.
    Field names and types align with GET /users/{id} so the frontend
    can use the same TypeScript type for both sources.
    """
    id:             str
    name:           str                 # full_name
    district:       str
    specialties:    List[str]           # crops grown (UserProfile.specialties)
    averageRating:  float               # FarmerStats.average_rating
    totalSales:     int                 # FarmerStats.total_sales
    totalListings:  int                 # FarmerStats.total_listings
    matchScore:     float               # ML-only ranking signal


class BuyerRecommendation(BaseModel):
    """
    Mirrors the user service UserProfile + BuyerStats buyer fields.
    """
    id:          str
    name:        str                    # full_name
    district:    str
    interests:   List[str]              # preferred crops (UserProfile.interests)
    totalOrders: int                    # BuyerStats.total_orders
    matchScore:  float                  # ML-only ranking signal


class FarmersForBuyerResponse(BaseModel):
    buyer_id:            str
    cached:              bool
    recommended_farmers: List[FarmerRecommendation]


class BuyersForFarmerResponse(BaseModel):
    farmer_id:           str
    cached:              bool
    recommended_buyers:  List[BuyerRecommendation]


class HealthResponse(BaseModel):
    status:         str
    service:        str
    farmers_loaded: int
    buyers_loaded:  int
