import threading
from collections import defaultdict

import structlog

log = structlog.get_logger()

# Boost applied per interaction event type
BOOST_MAP: dict[str, float] = {
    "farmer_viewed": 0.02,
    "buyer_inquiry": 0.05,
    "purchase_completed": 0.10,
    "high_rating": 0.08,
    "rating_submitted": 0.04,
}
MAX_BOOST = 0.20


class InteractionStore:
    """
    Thread-safe in-memory store of cumulative interaction boost scores.
    Updated in real-time from soko.interactions Kafka events.
    Key: (buyer_id, farmer_id) → float in [0.0, MAX_BOOST].
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._boosts: defaultdict[tuple[str, str], float] = defaultdict(float)

    def apply_event(self, event_type: str, buyer_id: str, farmer_id: str) -> None:
        delta = BOOST_MAP.get(event_type, 0.0)
        if delta == 0.0:
            log.debug("interaction_no_boost", event_type=event_type)
            return
        with self._lock:
            key = (buyer_id, farmer_id)
            self._boosts[key] = min(self._boosts[key] + delta, MAX_BOOST)
        log.info(
            "interaction_boost_applied",
            event_type=event_type,
            buyer_id=buyer_id,
            farmer_id=farmer_id,
            new_boost=self._boosts[(buyer_id, farmer_id)],
        )

    def get_boost(self, buyer_id: str, farmer_id: str) -> float:
        with self._lock:
            return self._boosts.get((buyer_id, farmer_id), 0.0)

    def get_all_boosts_for_buyer(self, buyer_id: str) -> dict[str, float]:
        with self._lock:
            return {fid: score for (bid, fid), score in self._boosts.items() if bid == buyer_id}

    def get_all_boosts_for_farmer(self, farmer_id: str) -> dict[str, float]:
        with self._lock:
            return {bid: score for (bid, fid), score in self._boosts.items() if fid == farmer_id}
