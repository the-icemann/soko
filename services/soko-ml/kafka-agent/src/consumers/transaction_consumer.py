import json
import os
import threading
from datetime import datetime

import structlog
from confluent_kafka import Consumer, KafkaError

from ..dlq import DLQHandler
from ..producers.event_producer import EventProducer

log = structlog.get_logger()


class TransactionConsumer:
    """
    Consumes soko.transactions (purchase_completed, order_cancelled).
    - Forwards enriched events to soko.interactions for recommendation boost.
    - Optionally triggers price model refresh on purchase_completed.
    Consumer group: soko-ml-rec-group
    """

    def __init__(self, producer: EventProducer, dlq: DLQHandler):
        self._producer = producer
        self._dlq = dlq
        self._stop_event = threading.Event()

        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._topic = os.getenv("KAFKA_TRANSACTION_TOPIC", "soko.transactions")
        self._interaction_topic = os.getenv("KAFKA_INTERACTION_TOPIC", "soko.interactions")

        self._consumer = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": "soko-ml-rec-group",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([self._topic])

    def run(self) -> None:
        log.info("transaction_consumer_started", topic=self._topic)
        try:
            while not self._stop_event.is_set():
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.error("transaction_consumer_error", error=str(msg.error()))
                    continue
                raw = msg.value().decode("utf-8")
                try:
                    data = json.loads(raw)
                    self._process(data)
                    self._consumer.commit(asynchronous=False)
                except Exception as exc:
                    log.error("transaction_processing_error", error=str(exc))
                    self._dlq.send(
                        original_topic=self._topic,
                        original_message=raw,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._consumer.commit(asynchronous=False)
            self._consumer.close()
            log.info("transaction_consumer_stopped")

    def _process(self, event: dict) -> None:
        event_type = event.get("event_type", "")
        buyer_id = event.get("buyer_id", "")
        farmer_id = event.get("farmer_id", "")

        if not (buyer_id and farmer_id):
            log.warning("transaction_event_missing_ids", event_type=event_type)
            return

        if event_type == "purchase_completed":
            # Publish an interaction event so the recommendation-service boosts this pair
            interaction_event = {
                "event_type": "purchase_completed",
                "buyer_id": buyer_id,
                "farmer_id": farmer_id,
                "timestamp": event.get("timestamp", datetime.utcnow().isoformat() + "Z"),
                "metadata": {
                    "crop": event.get("crop", ""),
                    "market": event.get("market", ""),
                    "quantity_kg": event.get("quantity_kg", 0),
                    "total_ugx": event.get("total_ugx", 0),
                },
            }
            self._producer.publish(
                self._interaction_topic,
                key=f"{buyer_id}_{farmer_id}",
                payload=interaction_event,
            )
            log.info("purchase_forwarded_to_interactions", buyer_id=buyer_id, farmer_id=farmer_id)

        elif event_type == "order_cancelled":
            log.info("order_cancelled_logged", buyer_id=buyer_id, farmer_id=farmer_id)

    def stop(self) -> None:
        self._stop_event.set()
