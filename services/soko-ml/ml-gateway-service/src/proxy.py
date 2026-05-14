import asyncio
import os
import time
from enum import Enum
from typing import Optional

import httpx
import structlog

log = structlog.get_logger()

PRICE_SERVICE_URL     = os.getenv("PRICE_SERVICE_URL",     "http://price-prediction-service:8001")
REC_SERVICE_URL       = os.getenv("REC_SERVICE_URL",       "http://recommendation-service:8002")
LOCATION_SERVICE_URL  = os.getenv("LOCATION_SERVICE_URL",  "http://location-service:8003")
INGEST_SERVICE_URL    = os.getenv("INGEST_SERVICE_URL",    "http://data-ingestion-service:8004")

MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 10.0


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Per-service circuit breaker.
    Opens after FAILURE_THRESHOLD consecutive failures.
    Transitions to HALF_OPEN after RESET_TIMEOUT seconds, allowing one probe.
    """

    FAILURE_THRESHOLD = 3
    RESET_TIMEOUT = 30.0

    def __init__(self, service_name: str):
        self.service_name = service_name
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None

    @property
    def state(self) -> CircuitState:
        if (
            self._state == CircuitState.OPEN
            and self._last_failure_time is not None
            and time.monotonic() - self._last_failure_time > self.RESET_TIMEOUT
        ):
            self._state = CircuitState.HALF_OPEN
            log.info("circuit_half_open", service=self.service_name)
        return self._state

    def record_success(self) -> None:
        if self._state != CircuitState.CLOSED:
            log.info("circuit_closed", service=self.service_name)
        self._state = CircuitState.CLOSED
        self._failure_count = 0

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.FAILURE_THRESHOLD:
            if self._state != CircuitState.OPEN:
                log.warning(
                    "circuit_opened",
                    service=self.service_name,
                    failures=self._failure_count,
                )
            self._state = CircuitState.OPEN

    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN


# One breaker per downstream service, keyed by service name
_breakers: dict[str, CircuitBreaker] = {
    "price-prediction": CircuitBreaker("price-prediction"),
    "recommendation":   CircuitBreaker("recommendation"),
    "location":         CircuitBreaker("location"),
    "ingestion":        CircuitBreaker("ingestion"),
}


def _get_breaker(url: str) -> CircuitBreaker:
    if PRICE_SERVICE_URL in url:
        return _breakers["price-prediction"]
    if LOCATION_SERVICE_URL in url:
        return _breakers["location"]
    if INGEST_SERVICE_URL in url:
        return _breakers["ingestion"]
    return _breakers["recommendation"]


async def proxy_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    json_body: Optional[dict] = None,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> tuple[dict, int]:
    """
    Proxy a request with retries and circuit breaker protection.
    Returns (response_body_dict, http_status_code).
    Returns a graceful fallback on circuit-open or exhausted retries.
    """
    breaker = _get_breaker(url)

    if breaker.is_open():
        log.warning("circuit_open_fast_fail", url=url)
        return _fallback_response(url), 503

    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if method.upper() == "POST":
                resp = await client.post(url, json=json_body, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
            else:
                resp = await client.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)

            if resp.status_code < 500:
                breaker.record_success()
                return resp.json(), resp.status_code

            log.warning("downstream_5xx", url=url, status=resp.status_code, attempt=attempt)
            breaker.record_failure()
            last_exc = Exception(f"HTTP {resp.status_code}")

        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as exc:
            log.warning("proxy_attempt_failed", url=url, attempt=attempt, error=str(exc))
            breaker.record_failure()
            last_exc = exc

        if attempt < MAX_RETRIES:
            await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)

    log.error("all_retries_exhausted", url=url, error=str(last_exc))
    return _fallback_response(url), 503


def _fallback_response(url: str) -> dict:
    if "price" in url:
        return {
            "error":       "price-prediction-service unavailable",
            "message":     "Price prediction is temporarily unavailable. Please try again later.",
            "cached":      False,
            "predictions": [],
        }
    if "location" in url or "route" in url or "discover" in url:
        return {
            "error":           "location-service unavailable",
            "message":         "Market routing is temporarily unavailable. Please try again later.",
            "tier":            0,
            "ranked_markets":  [],
        }
    if "ingest" in url or "gaps" in url or "coverage" in url or "bootstrap" in url:
        return {
            "error":   "data-ingestion-service unavailable",
            "message": "Data ingestion service is temporarily unavailable.",
        }
    return {
        "error":               "recommendation-service unavailable",
        "message":             "Recommendations are temporarily unavailable. Please try again later.",
        "cached":              False,
        "recommended_farmers": [],
        "recommended_buyers":  [],
    }


def get_service_breaker_status() -> dict[str, str]:
    return {name: breaker.state.value for name, breaker in _breakers.items()}
