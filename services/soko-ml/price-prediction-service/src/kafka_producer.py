import json
import os

import structlog
from confluent_kafka import Producer, KafkaException

log = structlog.get_logger()


class PriceProducer:
    def __init__(self):
        bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
        self._producer = Producer({"bootstrap.servers": bootstrap, "acks": "all"})
        self._result_topic = os.getenv("KAFKA_PRICE_RESULT_TOPIC", "soko.price.results")

    def _delivery_report(self, err, msg) -> None:
        if err:
            log.error("kafka_delivery_failed", topic=msg.topic(), error=str(err))
        else:
            log.debug("kafka_delivered", topic=msg.topic(), partition=msg.partition(), offset=msg.offset())

    def publish_prediction(self, prediction_result: dict, request_id: str = "") -> None:
        payload = {**prediction_result, "event_type": "price_predicted", "request_id": request_id}
        key = f"{prediction_result.get('market')}_{prediction_result.get('crop')}"
        try:
            self._producer.produce(
                self._result_topic,
                key=key.encode("utf-8"),
                value=json.dumps(payload).encode("utf-8"),
                callback=self._delivery_report,
            )
            self._producer.poll(0)
        except KafkaException as exc:
            log.error("kafka_produce_error", topic=self._result_topic, error=str(exc))

    def flush(self) -> None:
        self._producer.flush()

    def close(self) -> None:
        self.flush()
