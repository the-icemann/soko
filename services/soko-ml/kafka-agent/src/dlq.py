import json
import os
from datetime import datetime

import structlog
from confluent_kafka import Producer, KafkaException

log = structlog.get_logger()


class DLQHandler:
    """
    Dead-letter queue handler.
    Publishes failed messages to soko.dlq with full error context.
    """

    def __init__(self):
        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._dlq_topic = os.getenv("KAFKA_DLQ_TOPIC", "soko.dlq")
        self._producer = Producer({"bootstrap.servers": bootstrap})

    def send(
        self,
        original_topic: str,
        original_message: str,
        error_type: str,
        error_message: str,
        retry_count: int = 0,
    ) -> None:
        payload = {
            "original_topic": original_topic,
            "original_message": original_message,
            "error_type": error_type,
            "error_message": error_message,
            "retry_count": retry_count,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        try:
            self._producer.produce(
                self._dlq_topic,
                value=json.dumps(payload).encode("utf-8"),
            )
            self._producer.poll(0)
            log.warning(
                "message_sent_to_dlq",
                original_topic=original_topic,
                error_type=error_type,
            )
        except KafkaException as exc:
            log.error("dlq_produce_failed", error=str(exc))

    def flush(self) -> None:
        self._producer.flush(5.0)

    def close(self) -> None:
        self.flush()
