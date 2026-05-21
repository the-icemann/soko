from typing import Optional
from pydantic import BaseModel


class RouteRequest(BaseModel):
    farmer_id:       str
    farmer_lat:      float
    farmer_lng:      float
    crop:            str
    quantity_kg:     Optional[float] = None  # None = specialty-only, no listing yet
    max_distance_km: float = 150.0


class MarketResult(BaseModel):
    market:                  str
    distance_km:             float
    transport_mode:          str
    transport_cost_per_kg_ugx: float
    predicted_price_ugx:     float
    net_value_per_kg_ugx:    float
    total_net_value_ugx:     float
    signal:                  str
    signal_reason:           str
    confidence:              str


class RouteResponse(BaseModel):
    farmer_id:           str
    crop:                str
    quantity_kg:         Optional[float]
    currency:            str = "UGX"
    tier:                int
    ranked_markets:      list[MarketResult]
    transport_disclaimer: str
    cached_distances:    bool
    tier_message:        Optional[str] = None


class DiscoverRequest(BaseModel):
    buyer_id:        str
    buyer_lat:       float
    buyer_lng:       float
    crop:            str
    max_price_ugx:   float
    max_distance_km: float = 100.0
    top_n:           int   = 5


class FarmerResult(BaseModel):
    farmer_id:               str
    farmer_name:             str
    distance_km:             Optional[float]
    asking_price_ugx:        Optional[float]
    current_market_price_ugx: Optional[float]
    price_vs_market:         str
    avg_rating:              float
    fulfillment_rate:        float
    available_quantity_kg:   Optional[float]


class DiscoverResponse(BaseModel):
    buyer_id: str
    crop:     str
    results:  list[FarmerResult]
