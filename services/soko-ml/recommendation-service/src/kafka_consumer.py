import json
import os
import threading

import structlog
from confluent_kafka import Consumer, KafkaError

from .interaction_store import InteractionStore

log = structlog.get_logger()


class InteractionConsumer:
    """
    Consumes soko.interactions Kafka topic and:
    1. Updates the in-memory InteractionStore with real-time boost scores.
    2. Invalidates relevant Redis recommendation cache keys.
    """

    def __init__(self, interaction_store: InteractionStore):
        self._store = interaction_store
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._topic = os.getenv("KAFKA_INTERACTION_TOPIC", "soko.interactions")
        self._consumer = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": "soko-ml-rec-group",
            "auto.offset.reset": "latest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([self._topic])

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("interaction_consumer_started", topic=self._topic)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.error("interaction_consumer_error", error=str(msg.error()))
                    continue
                try:
                    data = json.loads(msg.value().decode("utf-8"))
                    event_type = data.get("event_type", "")
                    buyer_id = data.get("buyer_id", "")
                    farmer_id = data.get("farmer_id", "")

                    if buyer_id and farmer_id:
                        self._store.apply_event(event_type, buyer_id, farmer_id)
                        self._invalidate_redis(event_type, buyer_id, farmer_id)

                    self._consumer.commit(asynchronous=False)
                except Exception as exc:
                    log.error("interaction_event_processing_error", error=str(exc))
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._consumer.commit(asynchronous=False)
            self._consumer.close()
            log.info("interaction_consumer_stopped")

    def _invalidate_redis(self, event_type: str, buyer_id: str, farmer_id: str) -> None:
        """Synchronous Redis cache invalidation from the consumer thread."""
        import redis as sync_redis

        host = os.getenv("REDIS_HOST", "redis")
        port = int(os.getenv("REDIS_PORT", "6379"))
        db = int(os.getenv("REDIS_DB", "0"))
        password = os.getenv("REDIS_PASSWORD") or None
        try:
            r = sync_redis.Redis(host=host, port=port, db=db, password=password, decode_responses=True)
            if event_type in ("purchase_completed", "buyer_inquiry", "farmer_viewed"):
                for key in r.scan_iter(f"rec:farmers:{buyer_id}:*"):
                    r.delete(key)
                for key in r.scan_iter(f"rec:buyers:{farmer_id}:*"):
                    r.delete(key)
            elif event_type == "rating_submitted":
                for key in r.scan_iter(f"rec:farmers:{buyer_id}:*"):
                    r.delete(key)
            r.close()
            log.debug("redis_cache_invalidated", event_type=event_type, buyer_id=buyer_id, farmer_id=farmer_id)
        except Exception as exc:
            log.warning("redis_invalidate_error", error=str(exc))
