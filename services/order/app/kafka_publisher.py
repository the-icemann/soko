import json
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

_producer = None


def _get_producer():
    global _producer
    if _producer is not None:
        return _producer
    try:
        from confluent_kafka import Producer
        from app.core.config import settings
        _producer = Producer({
            "bootstrap.servers": settings.KAFKA_BOOTSTRAP_SERVERS,
            "socket.timeout.ms": 3000,
            "message.timeout.ms": 5000,
        })
        logger.info(f"Kafka producer connected: {settings.KAFKA_BOOTSTRAP_SERVERS}")
    except Exception as exc:
        logger.warning(f"Kafka producer init failed (ML events disabled): {exc}")
    return _producer


def _on_delivery(err, msg):
    if err:
        logger.warning(f"Kafka delivery failed: topic={msg.topic()} err={err}")


def publish_transaction(
    *,
    event_type: str,
    order_id: str,
    buyer_id: str,
    farmer_id: str,
    crop: str,
    market: str,
    quantity_kg: float,
    price_per_kg_ugx: float,
    total_ugx: float,
    product_name: str = "",
) -> None:
    """Fire-and-forget publish to soko.transactions. Silently skips if broker unreachable."""
    producer = _get_producer()
    if producer is None:
        return

    payload = json.dumps({
        "event_type": event_type,
        "order_id": order_id,
        "buyer_id": buyer_id,
        "farmer_id": farmer_id,
        "crop": crop,
        "product_name": product_name,
        "market": market,
        "quantity_kg": quantity_kg,
        "price_per_kg_ugx": price_per_kg_ugx,
        "total_ugx": total_ugx,
        "timestamp": datetime.utcnow().isoformat(),
    }).encode()

    try:
        from app.core.config import settings
        producer.produce(
            topic=settings.KAFKA_TRANSACTION_TOPIC,
            key=order_id.encode(),
            value=payload,
            callback=_on_delivery,
        )
        producer.poll(0)
    except Exception as exc:
        logger.warning(f"Kafka publish failed for order {order_id}: {exc}")
