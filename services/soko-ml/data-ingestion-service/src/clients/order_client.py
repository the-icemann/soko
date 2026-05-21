"""
Async HTTP client for order-service internal API.
Fetches delivered orders for price observation bootstrap.
"""
import os
import logging
from typing import AsyncIterator

import httpx

log = logging.getLogger(__name__)

ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL", "http://order-service:8004")
INTERNAL_API_KEY  = os.getenv("INTERNAL_API_KEY", "")
PAGE_LIMIT = 100


def _headers() -> dict:
    return {"x-internal-secret": INTERNAL_API_KEY}


async def fetch_delivered_orders() -> AsyncIterator[dict]:
    """
    Paginates through GET /internal/orders?status=delivered and yields each order dict.
    Falls back gracefully if the internal endpoint is not yet available.
    """
    page = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                resp = await client.get(
                    f"{ORDER_SERVICE_URL}/internal/orders",
                    params={"status": "delivered", "page": page, "limit": PAGE_LIMIT},
                    headers=_headers(),
                )
                if resp.status_code == 404:
                    log.warning(
                        "order-service /internal/orders endpoint not found — "
                        "price bootstrap from orders skipped. "
                        "Price observations will come from live soko.transactions events."
                    )
                    return
                resp.raise_for_status()
                orders = resp.json()
            except httpx.HTTPError as exc:
                log.error(f"Failed to fetch orders page {page}: {exc}")
                return

            if not orders:
                return

            for order in orders:
                yield order

            if len(orders) < PAGE_LIMIT:
                return
            page += 1
