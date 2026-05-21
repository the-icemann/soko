"""
Async HTTP client for user-service.
Fetches farmer and buyer profiles for bootstrap.
"""
import os
import logging
from typing import AsyncIterator

import httpx

log = logging.getLogger(__name__)

USER_SERVICE_URL = os.getenv("USER_SERVICE_URL", "http://user-service:8002")
INTERNAL_API_KEY = os.getenv("INTERNAL_API_KEY", "")
PAGE_LIMIT = 100


def _headers() -> dict:
    return {"x-internal-secret": INTERNAL_API_KEY}


async def fetch_all_farmers() -> AsyncIterator[dict]:
    """Paginates through GET /users/farmers and yields each farmer profile dict."""
    page = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                resp = await client.get(
                    f"{USER_SERVICE_URL}/users/farmers",
                    params={"page": page, "limit": PAGE_LIMIT},
                    headers=_headers(),
                )
                resp.raise_for_status()
                profiles = resp.json()
            except httpx.HTTPError as exc:
                log.error(f"Failed to fetch farmers page {page}: {exc}")
                return

            if not profiles:
                return

            for profile in profiles:
                yield profile

            if len(profiles) < PAGE_LIMIT:
                return
            page += 1


async def fetch_all_buyers() -> AsyncIterator[dict]:
    """Paginates through GET /users/buyers and yields each buyer profile dict."""
    page = 1
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                resp = await client.get(
                    f"{USER_SERVICE_URL}/users/buyers",
                    params={"page": page, "limit": PAGE_LIMIT},
                    headers=_headers(),
                )
                resp.raise_for_status()
                profiles = resp.json()
            except httpx.HTTPError as exc:
                log.error(f"Failed to fetch buyers page {page}: {exc}")
                return

            if not profiles:
                return

            for profile in profiles:
                yield profile

            if len(profiles) < PAGE_LIMIT:
                return
            page += 1
