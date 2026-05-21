"""
Async HTTP client for produce-service (listing-service).
Used to scan the active listing catalogue for coverage map seeding.
"""
import os
import logging
from typing import AsyncIterator

import httpx

log = logging.getLogger(__name__)

LISTING_SERVICE_URL = os.getenv("LISTING_SERVICE_URL", "http://produce-service:3004")
INTERNAL_API_KEY    = os.getenv("INTERNAL_API_KEY", "")
PAGE_LIMIT = 100


def _headers() -> dict:
    return {"x-internal-secret": INTERNAL_API_KEY}


async def fetch_all_listings() -> AsyncIterator[dict]:
    """
    Paginates through GET /listings (public endpoint) and yields each listing dict.
    Produce-service uses /listings not /internal/listings.
    """
    page = 1
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        while True:
            try:
                resp = await client.get(
                    f"{LISTING_SERVICE_URL}/listings/",
                    params={"page": page, "limit": PAGE_LIMIT},
                )
                resp.raise_for_status()
                listings = resp.json()
            except httpx.HTTPError as exc:
                log.error(f"Failed to fetch listings page {page}: {exc}")
                return

            if not listings:
                return

            for listing in listings:
                yield listing

            if len(listings) < PAGE_LIMIT:
                return
            page += 1
