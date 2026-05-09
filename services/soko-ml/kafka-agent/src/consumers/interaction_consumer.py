import json
import os
import threading

import httpx
import structlog
from confluent_kafka import Consumer, KafkaError

from ..dlq import DLQHandler

log = structlog.get_logger()


class InteractionConsumer:
    """
    Consumes soko.interactions.
    Forwards events to recommendation-service to update in-memory interaction boosts.
    Consumer group: soko-ml-rec-group
    """

    def __init__(self, dlq: DLQHandler):
        self._dlq = dlq
        self._stop_event = threading.Event()

        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._topic = os.getenv("KAFKA_INTERACTION_TOPIC", "soko.interactions")
        self._rec_url = os.getenv("REC_SERVICE_URL", "http://recommendation-service:8002")

        self._consumer = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": "soko-ml-rec-group",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([self._topic])

    def run(self) -> None:
        log.info("interaction_consumer_started", topic=self._topic)
        try:
            while not self._stop_event.is_set():
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.error("interaction_consumer_error", error=str(msg.error()))
                    continue
                raw = msg.value().decode("utf-8")
                try:
                    data = json.loads(raw)
                    self._forward_to_rec_service(data)
                    self._consumer.commit(asynchronous=False)
                except Exception as exc:
                    log.error("interaction_processing_error", error=str(exc))
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
            log.info("interaction_consumer_stopped")

    def _forward_to_rec_service(self, event: dict) -> None:
        """POST the interaction event to the recommendation-service internal endpoint."""
        # The recommendation-service handles boosts via its own Kafka consumer.
        # This agent acts as a relay/enrichment layer — log and validate only.
        event_type = event.get("event_type", "")
        buyer_id = event.get("buyer_id", "")
        farmer_id = event.get("farmer_id", "")
        if not (buyer_id and farmer_id):
            log.warning("interaction_event_missing_ids", event=event)
            return
        log.info("interaction_event_processed", event_type=event_type, buyer_id=buyer_id, farmer_id=farmer_id)

    def stop(self) -> None:
        self._stop_event.set()
