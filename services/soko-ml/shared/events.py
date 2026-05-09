from dataclasses import dataclass, field
from datetime import datetime
import json


@dataclass
class PriceRequestEvent:
    market: str
    crop: str
    weeks_ahead: int
    request_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_json(self) -> str:
        return json.dumps({
            "event_type": "price_prediction_requested",
            "market": self.market,
            "crop": self.crop,
            "weeks_ahead": self.weeks_ahead,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_dict(cls, data: dict) -> "PriceRequestEvent":
        return cls(
            market=data["market"],
            crop=data["crop"],
            weeks_ahead=data.get("weeks_ahead", 4),
            request_id=data.get("request_id", ""),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
        )


@dataclass
class PriceResultEvent:
    market: str
    crop: str
    currency: str
    price_type: str
    cached: bool
    predictions: list
    request_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_json(self) -> str:
        return json.dumps({
            "event_type": "price_predicted",
            "market": self.market,
            "crop": self.crop,
            "currency": self.currency,
            "price_type": self.price_type,
            "cached": self.cached,
            "predictions": self.predictions,
            "request_id": self.request_id,
            "timestamp": self.timestamp,
        })


@dataclass
class InteractionEvent:
    event_type: str  # farmer_viewed, buyer_inquiry, rating_submitted, purchase_completed
    buyer_id: str
    farmer_id: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    metadata: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "event_type": self.event_type,
            "buyer_id": self.buyer_id,
            "farmer_id": self.farmer_id,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        })

    @classmethod
    def from_dict(cls, data: dict) -> "InteractionEvent":
        return cls(
            event_type=data["event_type"],
            buyer_id=data["buyer_id"],
            farmer_id=data["farmer_id"],
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class TransactionEvent:
    event_type: str  # purchase_completed, order_cancelled
    buyer_id: str
    farmer_id: str
    crop: str
    market: str
    quantity_kg: float
    price_per_kg_ugx: float
    total_ugx: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_json(self) -> str:
        return json.dumps({
            "event_type": self.event_type,
            "buyer_id": self.buyer_id,
            "farmer_id": self.farmer_id,
            "crop": self.crop,
            "market": self.market,
            "quantity_kg": self.quantity_kg,
            "price_per_kg_ugx": self.price_per_kg_ugx,
            "total_ugx": self.total_ugx,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_dict(cls, data: dict) -> "TransactionEvent":
        return cls(
            event_type=data["event_type"],
            buyer_id=data["buyer_id"],
            farmer_id=data["farmer_id"],
            crop=data["crop"],
            market=data["market"],
            quantity_kg=float(data.get("quantity_kg", 0)),
            price_per_kg_ugx=float(data.get("price_per_kg_ugx", 0)),
            total_ugx=float(data.get("total_ugx", 0)),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
        )


@dataclass
class MLEvent:
    event_type: str  # retrain_requested, model_deployed
    market: str = ""
    crop: str = ""
    metadata: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_json(self) -> str:
        return json.dumps({
            "event_type": self.event_type,
            "market": self.market,
            "crop": self.crop,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        })

    @classmethod
    def from_dict(cls, data: dict) -> "MLEvent":
        return cls(
            event_type=data["event_type"],
            market=data.get("market", ""),
            crop=data.get("crop", ""),
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
        )


@dataclass
class DLQEvent:
    original_topic: str
    original_message: str
    error_type: str
    error_message: str
    retry_count: int = 0
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    def to_json(self) -> str:
        return json.dumps({
            "original_topic": self.original_topic,
            "original_message": self.original_message,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "timestamp": self.timestamp,
        })
