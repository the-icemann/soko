from pydantic import BaseModel, Field
from typing import List


class PredictionRequest(BaseModel):
    market: str = Field(..., examples=["Kisenyi_Kampala"])
    crop: str = Field(..., examples=["maize_grain"])
    weeks_ahead: int = Field(default=4, ge=1, le=52)


class WeeklyPrediction(BaseModel):
    date: str
    predicted_price_ugx: int
    lower_bound: int
    upper_bound: int


class PredictionResponse(BaseModel):
    market: str
    crop: str
    currency: str = "UGX"
    price_type: str = "wholesale"
    cached: bool
    predictions: List[WeeklyPrediction]


class HealthResponse(BaseModel):
    status: str
    service: str
    models_loaded: int


class MarketsResponse(BaseModel):
    markets: List[str]


class CropsResponse(BaseModel):
    crops: List[str]
