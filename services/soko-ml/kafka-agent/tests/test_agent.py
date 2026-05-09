import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.dlq import DLQHandler
from src.producers.event_producer import EventProducer


# ── DLQ tests ─────────────────────────────────────────────────────────────────

@patch("src.dlq.Producer")
def test_dlq_send_produces_message(mock_producer_cls):
    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer

    dlq = DLQHandler()
    dlq.send(
        original_topic="soko.transactions",
        original_message='{"event_type": "purchase_completed"}',
        error_type="ValueError",
        error_message="bad data",
    )
    mock_producer.produce.assert_called_once()
    call_kwargs = mock_producer.produce.call_args
    assert call_kwargs[0][0] == dlq._dlq_topic


@patch("src.dlq.Producer")
def test_dlq_payload_has_required_fields(mock_producer_cls):
    import json
    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer

    dlq = DLQHandler()
    dlq.send(
        original_topic="soko.interactions",
        original_message='{}',
        error_type="KeyError",
        error_message="missing buyer_id",
        retry_count=2,
    )
    call_kwargs = mock_producer.produce.call_args[1]
    payload = json.loads(call_kwargs["value"].decode("utf-8"))
    assert payload["original_topic"] == "soko.interactions"
    assert payload["error_type"] == "KeyError"
    assert payload["retry_count"] == 2
    assert "timestamp" in payload


# ── EventProducer tests ───────────────────────────────────────────────────────

@patch("src.producers.event_producer.Producer")
def test_event_producer_publish(mock_producer_cls):
    import json
    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer

    ep = EventProducer()
    ep.publish("soko.price.results", "Gulu_maize_grain", {"market": "Gulu", "crop": "maize_grain"})

    mock_producer.produce.assert_called_once()
    args = mock_producer.produce.call_args[0]
    assert args[0] == "soko.price.results"


@patch("src.producers.event_producer.Producer")
def test_event_producer_key_encoding(mock_producer_cls):
    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer

    ep = EventProducer()
    ep.publish("soko.interactions", "B0001_F0012", {"event_type": "farmer_viewed"})

    call_kwargs = mock_producer.produce.call_args[1]
    assert call_kwargs["key"] == b"B0001_F0012"


# ── TransactionConsumer processing logic ──────────────────────────────────────

@patch("src.producers.event_producer.Producer")
@patch("src.dlq.Producer")
def test_transaction_consumer_forwards_purchase(mock_dlq_producer_cls, mock_producer_cls):
    from src.consumers.transaction_consumer import TransactionConsumer

    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer
    mock_dlq_producer_cls.return_value = MagicMock()

    ep = EventProducer()
    dlq = DLQHandler()
    consumer = TransactionConsumer(ep, dlq)

    consumer._process({
        "event_type": "purchase_completed",
        "buyer_id": "B0001",
        "farmer_id": "F0012",
        "crop": "maize_grain",
        "market": "Kisenyi_Kampala",
        "quantity_kg": 200,
        "price_per_kg_ugx": 1280,
        "total_ugx": 256000,
        "timestamp": "2025-06-01T14:30:00Z",
    })

    # Should have published to soko.interactions
    mock_producer.produce.assert_called()
    call_args = mock_producer.produce.call_args[0]
    assert "interactions" in call_args[0]


@patch("src.producers.event_producer.Producer")
@patch("src.dlq.Producer")
def test_transaction_consumer_skips_missing_ids(mock_dlq_producer_cls, mock_producer_cls):
    from src.consumers.transaction_consumer import TransactionConsumer

    mock_producer = MagicMock()
    mock_producer_cls.return_value = mock_producer
    mock_dlq_producer_cls.return_value = MagicMock()

    ep = EventProducer()
    dlq = DLQHandler()
    consumer = TransactionConsumer(ep, dlq)

    consumer._process({"event_type": "purchase_completed"})
    mock_producer.produce.assert_not_called()
