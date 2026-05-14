"""
Consumes soko.gaps — coverage gap farmer and admin notification events.
Logs them for monitoring and forwards to the notification pipeline if configured.
Consumer group: soko-ml-gaps-group
"""
import json
import logging
import os
import threading

from confluent_kafka import Consumer, KafkaError

log = logging.getLogger(__name__)


class CoverageGapConsumer:
    """
    Reads coverage gap events published by location-service.
    Primarily exists for observability — logs every new/escalating gap.
    Extend to forward admin events to a notification webhook if needed.
    """

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        bootstrap        = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._topic      = os.getenv("KAFKA_GAPS_TOPIC", "soko.gaps")
        self._consumer   = Consumer({
            "bootstrap.servers": bootstrap,
            "group.id":          "soko-ml-gaps-group",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([self._topic])

    def run(self) -> None:
        log.info(f"coverage_gap_consumer_started topic={self._topic}")
        try:
            while not self._stop_event.is_set():
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.error(f"coverage_gap_consumer_error: {msg.error()}")
                    continue

                raw = msg.value().decode("utf-8")
                try:
                    event = json.loads(raw)
                    self._process(event)
                    self._consumer.commit(asynchronous=False)
                except Exception as exc:
                    log.error(f"gap_event_processing_error: {exc}")
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._consumer.close()
            log.info("coverage_gap_consumer_stopped")

    def _process(self, event: dict) -> None:
        recipient = event.get("recipient", "")
        crop      = event.get("crop_submitted", "")
        priority  = event.get("priority", "low")
        frequency = event.get("frequency", 1)

        if recipient == "admin":
            log.info(
                f"coverage_gap_admin_alert "
                f"crop={crop} priority={priority} frequency={frequency} "
                f"category_guess={event.get('category_guess', '')}"
            )
        elif recipient == "farmer":
            log.info(
                f"coverage_gap_farmer_notified "
                f"farmer_id={event.get('farmer_id', '')} crop={crop}"
            )

    def stop(self) -> None:
        self._stop_event.set()
