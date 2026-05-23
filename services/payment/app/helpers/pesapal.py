import logging
import httpx
from datetime import datetime, timedelta
from app.core.config import settings

logger = logging.getLogger(__name__)

PESAPAL_BASE = (
    "https://cybqa.pesapal.com/pesapalv3"
    if settings.PESAPAL_SANDBOX else
    "https://pay.pesapal.com/v3"
)

# In-memory token cache — avoids hitting PesaPal auth on every request
_token_cache: dict = {"token": None, "expires_at": None}


async def get_access_token() -> str:
    """
    Fetches a PesaPal OAuth token.
    Caches it until 5 minutes before expiry.
    """
    now = datetime.utcnow()

    if _token_cache["token"] and _token_cache["expires_at"]:
        if now < _token_cache["expires_at"] - timedelta(minutes=5):
            return _token_cache["token"]

    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{PESAPAL_BASE}/api/Auth/RequestToken",
            json={
                "consumer_key":    settings.PESAPAL_CONSUMER_KEY,
                "consumer_secret": settings.PESAPAL_CONSUMER_SECRET,
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=10.0
        )
        res.raise_for_status()
        data = res.json()

    token   = data["token"]
    expiry  = datetime.fromisoformat(data["expiryDate"].replace("Z", ""))

    _token_cache["token"]      = token
    _token_cache["expires_at"] = expiry

    logger.info("PesaPal token refreshed")
    return token


async def register_ipn_url() -> str:
    """
    Registers your IPN URL with PesaPal.
    Returns the ipn_id — store this and reuse it.
    Call this once on startup if not already registered.
    """
    token = await get_access_token()

    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{PESAPAL_BASE}/api/URLSetup/RegisterIPN",
            json={
                "url":          settings.PESAPAL_IPN_URL,
                "ipn_notification_type": "GET",
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
                "Content-Type":  "application/json",
            },
            timeout=10.0
        )
        res.raise_for_status()
        data = res.json()

    logger.info(f"IPN registered: {data['ipn_id']}")
    return data["ipn_id"]


async def submit_order(
    merchant_ref:  str,
    amount:        float,
    currency:      str,
    description:   str,
    buyer_email:   str,
    buyer_phone:   str,
    buyer_name:    str,
    ipn_id:        str,
    callback_url:  str,
) -> dict:
    """
    Submits an order to PesaPal.
    Returns { order_tracking_id, redirect_url }.
    """
    token = await get_access_token()

    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{PESAPAL_BASE}/api/Transactions/SubmitOrderRequest",
            json={
                "id":           merchant_ref,
                "currency":     currency,
                "amount":       amount,
                "description":  description,
                "callback_url": callback_url,
                "notification_id": ipn_id,
                "billing_address": {
                    "email_address": buyer_email,
                    "phone_number":  buyer_phone,
                    "first_name":    buyer_name.split()[0],
                    "last_name":     buyer_name.split()[-1] if len(buyer_name.split()) > 1 else "",
                }
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
                "Content-Type":  "application/json",
            },
            timeout=10.0
        )
        res.raise_for_status()
        return res.json()


async def get_transaction_status(order_tracking_id: str) -> dict:
    """
    Fetches the current status of a PesaPal transaction.
    Returns PesaPal's status object.
    """
    token = await get_access_token()

    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{PESAPAL_BASE}/api/Transactions/GetTransactionStatus",
            params={"orderTrackingId": order_tracking_id},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept":        "application/json",
            },
            timeout=10.0
        )
        res.raise_for_status()
        return res.json()