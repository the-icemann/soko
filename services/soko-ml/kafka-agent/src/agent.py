"""
Kafka Agent — event backbone for the Soko ML layer.
Starts all consumer threads and manages lifecycle.
"""
import os
import signal
import sys
import threading

import structlog

from .consumers.interaction_consumer import InteractionConsumer
from .consumers.transaction_consumer import TransactionConsumer
from .consumers.price_request_consumer import PriceRequestConsumer
from .dlq import DLQHandler
from .producers.event_producer import EventProducer

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()


class KafkaAgent:
    def __init__(self):
        self._dlq = DLQHandler()
        self._producer = EventProducer()

        self._consumers = [
            TransactionConsumer(self._producer, self._dlq),
            InteractionConsumer(self._dlq),
            PriceRequestConsumer(self._producer, self._dlq),
        ]
        self._threads: list[threading.Thread] = []
        self._shutdown_event = threading.Event()

    def start(self) -> None:
        log.info("kafka_agent_starting", consumers=len(self._consumers))
        for consumer in self._consumers:
            t = threading.Thread(target=consumer.run, daemon=True)
            t.start()
            self._threads.append(t)
        log.info("kafka_agent_started")

    def wait(self) -> None:
        """Block until shutdown signal."""
        try:
            self._shutdown_event.wait()
        except (KeyboardInterrupt, SystemExit):
            pass

    def stop(self) -> None:
        log.info("kafka_agent_stopping")
        for consumer in self._consumers:
            consumer.stop()
        for t in self._threads:
            t.join(timeout=15)
        self._producer.close()
        self._dlq.close()
        log.info("kafka_agent_stopped")


def main() -> None:
    agent = KafkaAgent()

    def _handle_signal(signum, frame):
        log.info("shutdown_signal_received", signal=signum)
        agent._shutdown_event.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    agent.start()
    agent.wait()
    agent.stop()
    sys.exit(0)


if __name__ == "__main__":
    main()
