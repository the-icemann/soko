import json
import os

import structlog
from confluent_kafka import Producer, KafkaException

log = structlog.get_logger()


class EventProducer:
    """Generic Kafka producer used by the kafka-agent to publish events."""

    def __init__(self):
        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._producer = Producer({"bootstrap.servers": bootstrap, "acks": "all"})

    def _delivery_report(self, err, msg) -> None:
        if err:
            log.error("kafka_delivery_failed", topic=msg.topic(), error=str(err))
        else:
            log.debug("kafka_delivered", topic=msg.topic(), offset=msg.offset())

    def publish(self, topic: str, key: str, payload: dict) -> None:
        try:
            self._producer.produce(
                topic,
                key=key.encode("utf-8"),
                value=json.dumps(payload).encode("utf-8"),
                callback=self._delivery_report,
            )
            self._producer.poll(0)
        except KafkaException as exc:
            log.error("kafka_produce_error", topic=topic, key=key, error=str(exc))

    def flush(self, timeout: float = 5.0) -> None:
        self._producer.flush(timeout)

    def close(self) -> None:
        self.flush()
