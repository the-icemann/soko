import json
import os
import threading

import httpx
import structlog
from confluent_kafka import Consumer, KafkaError

from ..dlq import DLQHandler
from ..producers.event_producer import EventProducer

log = structlog.get_logger()


class PriceRequestConsumer:
    """
    Consumes soko.price.requests (price_prediction_requested).
    Calls price-prediction-service and publishes the result to soko.price.results.
    Consumer group: soko-ml-price-group
    """

    def __init__(self, producer: EventProducer, dlq: DLQHandler):
        self._producer = producer
        self._dlq = dlq
        self._stop_event = threading.Event()

        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._topic = os.getenv("KAFKA_PRICE_REQUEST_TOPIC", "soko.price.requests")
        self._result_topic = os.getenv("KAFKA_PRICE_RESULT_TOPIC", "soko.price.results")
        self._price_url = os.getenv("PRICE_SERVICE_URL", "http://price-prediction-service:8001")

        self._consumer = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id": "soko-ml-price-group",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([self._topic])

    def run(self) -> None:
        log.info("price_request_consumer_started", topic=self._topic)
        try:
            while not self._stop_event.is_set():
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.error("price_request_consumer_error", error=str(msg.error()))
                    continue
                raw = msg.value().decode("utf-8")
                try:
                    data = json.loads(raw)
                    self._process(data)
                    self._consumer.commit(asynchronous=False)
                except Exception as exc:
                    log.error("price_request_processing_error", error=str(exc))
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
            log.info("price_request_consumer_stopped")

    def _process(self, event: dict) -> None:
        market = event.get("market", "")
        crop = event.get("crop", "")
        weeks_ahead = int(event.get("weeks_ahead", 4))
        request_id = event.get("request_id", "")

        if not (market and crop):
            log.warning("price_request_missing_fields", event=event)
            return

        try:
            resp = httpx.post(
                f"{self._price_url}/predict",
                json={"market": market, "crop": crop, "weeks_ahead": weeks_ahead},
                timeout=15.0,
            )
            if resp.status_code == 200:
                result = resp.json()
                result["request_id"] = request_id
                self._producer.publish(
                    self._result_topic,
                    key=f"{market}_{crop}",
                    payload=result,
                )
                log.info("price_result_published", market=market, crop=crop)
            else:
                log.warning("price_service_non_200", status=resp.status_code, market=market, crop=crop)
        except httpx.RequestError as exc:
            log.error("price_service_request_error", error=str(exc), market=market, crop=crop)
            raise

    def stop(self) -> None:
        self._stop_event.set()
