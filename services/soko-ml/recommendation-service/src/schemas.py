from pydantic import BaseModel
from typing import List


class FarmerRecommendation(BaseModel):
    farmer_id: str
    name: str
    crops_offered: List[str]
    avg_rating: float
    fulfillment_rate: float
    match_score: float


class BuyerRecommendation(BaseModel):
    buyer_id: str
    name: str
    preferred_crops: List[str]
    payment_reliability: float
    avg_order_volume_kg: float
    match_score: float


class FarmersForBuyerResponse(BaseModel):
    buyer_id: str
    cached: bool
    recommended_farmers: List[FarmerRecommendation]


class BuyersForFarmerResponse(BaseModel):
    farmer_id: str
    cached: bool
    recommended_buyers: List[BuyerRecommendation]


class HealthResponse(BaseModel):
    status: str
    service: str
    farmers_loaded: int
    buyers_loaded: int
