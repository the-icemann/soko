"""
Consumes soko.transactions in real time.
Processes purchase_completed events → price observations in the feature store.
Also updates buyer purchase counts to improve recommendation quality.
"""
import json
import logging
import os
import threading

from confluent_kafka import Consumer, KafkaError, Producer

from ..transformers.price_transformer import transform_transaction_event
from ..transformers.farmer_transformer import DISTRICT_TO_MARKET, DEFAULT_MARKET

log = logging.getLogger(__name__)

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
TRANSACTION_TOPIC = os.getenv("KAFKA_TRANSACTION_TOPIC", "soko.transactions")
ML_EVENTS_TOPIC   = os.getenv("KAFKA_ML_EVENTS_TOPIC", "soko.ml.events")
MIN_OBSERVATIONS_FOR_RETRAIN = int(os.getenv("MIN_OBSERVATIONS_FOR_MODEL", "52"))


class TransactionStream:
    """
    Runs in its own thread. Consumes soko.transactions and writes price observations.
    Publishes retrain_requested to soko.ml.events when a pair hits the threshold.
    """

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._consumer = Consumer({
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": "soko-ml-ingest-transactions",
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe([TRANSACTION_TOPIC])

        self._producer = Producer({
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "socket.timeout.ms": 3000,
            "message.timeout.ms": 5000,
        })

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self._run, daemon=True, name="transaction-stream")
        t.start()
        return t

    def stop(self) -> None:
        self._stop_event.set()

    def _run(self) -> None:
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        log.info(f"transaction_stream_started topic={TRANSACTION_TOPIC}")
        try:
            while not self._stop_event.is_set():
                msg = self._consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.error(f"transaction_stream_error: {msg.error()}")
                    continue

                raw = msg.value().decode("utf-8")
                try:
                    event = json.loads(raw)
                    loop.run_until_complete(self._process(event))
                    self._consumer.commit(asynchronous=False)
                except Exception as exc:
                    log.error(f"transaction_processing_error: {exc}")
        finally:
            self._consumer.close()
            loop.close()
            log.info("transaction_stream_stopped")

    async def _process(self, event: dict) -> None:
        from ..feature_store import insert_price_observation, get_coverage_status, mark_retrained
        from datetime import datetime

        rec = transform_transaction_event(event)
        if rec is None:
            return

        inserted = await insert_price_observation(rec)
        if not inserted:
            return

        # Check if this pair just crossed the retrain threshold
        coverage = await get_coverage_status(rec["crop"], rec["market"])
        if (
            coverage
            and coverage["is_model_ready"]
            and coverage["last_retrain_at"] is None
        ):
            await mark_retrained(rec["crop"], rec["market"])
            self._publish_retrain_event(rec["crop"], rec["market"], coverage["observation_count"])

        # Increment buyer purchase count in feature store
        buyer_id = event.get("buyer_id", "")
        if buyer_id:
            await self._update_buyer_purchases(buyer_id)

    async def _update_buyer_purchases(self, buyer_id: str) -> None:
        from ..feature_store import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE buyer_features
                SET total_purchases = total_purchases + 1,
                    last_active_at  = NOW()
                WHERE buyer_id = $1
                """,
                buyer_id,
            )

    def _publish_retrain_event(self, crop: str, market: str, observation_count: int) -> None:
        from datetime import datetime
        payload = json.dumps({
            "event_type": "retrain_requested",
            "market": market,
            "crop": crop,
            "reason": f"{observation_count} real transaction observations reached",
            "data_source": "soko_order",
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }).encode()
        try:
            self._producer.produce(
                ML_EVENTS_TOPIC,
                key=f"{market}__{crop}".encode(),
                value=payload,
            )
            self._producer.poll(0)
            log.info(f"retrain_requested published: {market}/{crop}")
        except Exception as exc:
            log.warning(f"Failed to publish retrain event: {exc}")
