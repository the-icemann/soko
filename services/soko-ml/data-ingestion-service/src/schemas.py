from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel


class FarmerProfileIn(BaseModel):
    """Shape of a farmer profile as returned by user-service GET /users/farmers."""
    id: str
    name: str
    initials: str
    avatarUrl: Optional[str] = None
    district: str
    village: Optional[str] = None
    verified: bool = False
    farmerBio: Optional[str] = None
    farmName: Optional[str] = None
    specialties: list[str] = []
    memberSince: str = ""
    totalListings: int = 0
    totalSales: int = 0
    averageRating: float = 0.0
    totalReviews: int = 0
    responseTime: Optional[str] = None


class BuyerProfileIn(BaseModel):
    """Shape of a buyer profile as returned by user-service GET /users/buyers."""
    id: str
    name: str
    initials: str
    email: str
    phone: str
    avatarUrl: Optional[str] = None
    district: str
    village: Optional[str] = None
    role: str
    verified: bool = False
    memberSince: str = ""
    totalOrders: Optional[int] = 0
    totalSpent: Optional[int] = 0
    wishlistCount: Optional[int] = 0


class OrderItemIn(BaseModel):
    """Shape of an order item from order-service GET /internal/orders."""
    id: str
    product_id: str
    product_name: str
    farmer_id: str
    farmer_name: str
    unit: str
    category: str
    unit_price: float
    quantity: float
    subtotal: float


class OrderIn(BaseModel):
    """Shape of a completed order from order-service GET /internal/orders."""
    id: str
    buyer_id: str
    status: str
    delivery_district: str
    currency: str = "UGX"
    updated_at: str
    items: list[OrderItemIn] = []


class IngestAuthEventPayload(BaseModel):
    """Forwarded from kafka-agent for user profile events."""
    event_type: str
    user_id: str
    role: str
    data: dict = {}


class IngestOrderEventPayload(BaseModel):
    """Forwarded from soko.transactions consumer."""
    event_type: str
    order_id: str
    buyer_id: str
    farmer_id: str
    crop: str
    product_name: str = ""
    market: str
    quantity_kg: float
    price_per_kg_ugx: float
    total_ugx: float
    timestamp: str = ""


class BootstrapStatusResponse(BaseModel):
    farmers_ingested: int
    buyers_ingested: int
    orders_ingested: int
    coverage_pairs: int
    already_bootstrapped: bool


class UserCreatedPayload(BaseModel):
    """Sent by user-service after a new account is created."""
    id:          str
    role:        str
    full_name:   str
    district:    Optional[str]       = None
    specialties: Optional[List[str]] = None
    interests:   Optional[List[str]] = None
