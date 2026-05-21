"""
Consumes soko.transactions and forwards purchase_completed events to
data-ingestion-service via HTTP POST /ingest/order-event.

This is an alternative path to the TransactionStream inside data-ingestion-service itself.
Both can run simultaneously — data-ingestion-service deduplicates on order_id via the
insert_price_observation function (Postgres UNIQUE constraint on order_id per observation).

Consumer group: soko-ml-price-collector
"""
import json
import logging
import os
import threading

import httpx
from confluent_kafka import Consumer, KafkaError

from ..dlq import DLQHandler

log = logging.getLogger(__name__)


class TransactionPriceCollector:
    """
    Forwards purchase_completed events from soko.transactions
    to data-ingestion-service for price observation storage.
    """

    def __init__(self, dlq: DLQHandler) -> None:
        self._dlq          = dlq
        self._stop_event   = threading.Event()
        bootstrap          = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._topic        = os.getenv("KAFKA_TRANSACTION_TOPIC", "soko.transactions")
        self._ingest_url   = os.getenv("DATA_INGESTION_SERVICE_URL", "http://data-ingestion-service:8004")
        self._consumer     = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id":          "soko-ml-price-collector",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([self._topic])

    def run(self) -> None:
        log.info(f"transaction_price_collector_started topic={self._topic}")
        try:
            while not self._stop_event.is_set():
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.error(f"transaction_price_collector_error: {msg.error()}")
                    continue

                raw = msg.value().decode("utf-8")
                try:
                    event = json.loads(raw)
                    if event.get("event_type") == "purchase_completed":
                        self._forward(event)
                    self._consumer.commit(asynchronous=False)
                except Exception as exc:
                    log.error(f"price_collector_processing_error: {exc}")
                    self._dlq.send(
                        original_topic=self._topic,
                        original_message=raw,
                        error_type=type(exc).__name__,
                        error_message=str(exc),
                    )
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._consumer.close()
            log.info("transaction_price_collector_stopped")

    def _forward(self, event: dict) -> None:
        try:
            resp = httpx.post(
                f"{self._ingest_url}/ingest/order-event",
                json=event,
                timeout=5.0,
            )
            if resp.status_code not in (200, 201):
                log.warning(f"ingest_service_non_200 status={resp.status_code}")
        except httpx.RequestError as exc:
            log.warning(f"ingest_service_unreachable: {exc} — event will be retried on next poll")
            raise

    def stop(self) -> None:
        self._stop_event.set()
