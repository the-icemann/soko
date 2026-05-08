import uuid
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import httpx

from app.core.config import settings
from app.core.dependencies import internal_only
from app.db.database import get_db
from app.helpers.pesapal import submit_order, register_ipn_url
from app.models.payment import Transaction, PaymentStatus, PaymentMethodType
from app.schemas.payment import InitiatePaymentPayload, InitiatePaymentResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Internal"], dependencies=[Depends(internal_only)])

# IPN id — registered once on startup, reused for all orders
_ipn_id: str | None = None


async def get_ipn_id() -> str:
    global _ipn_id
    if not _ipn_id:
        _ipn_id = await register_ipn_url()
    return _ipn_id


async def fetch_buyer_details(buyer_id: str) -> dict:
    """Fetches buyer name, email and phone from User Service."""
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{settings.USER_SERVICE_URL}/internal/user/{buyer_id}",
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
            res.raise_for_status()
            return res.json()
    except Exception as e:
        logger.error(f"Could not fetch buyer {buyer_id}: {e}")
        return {}


@router.post("/initiate", response_model=InitiatePaymentResponse)
async def initiate_payment(
    payload: InitiatePaymentPayload,
    db:      Session = Depends(get_db)
):
    # ── 1. Prevent duplicate transactions for same order
    existing = db.query(Transaction).filter(
        Transaction.order_id == uuid.UUID(payload.order_id)
    ).first()
    if existing:
        return InitiatePaymentResponse(
            transaction_id=str(existing.id),
            payment_url=existing.pesapal_payment_url,
            status=existing.status,
            message="Payment already initiated"
        )

    # ── 2. Cash on delivery — no PesaPal needed
    if payload.payment_method.type == "cash_on_delivery":
        tx = Transaction(
            order_id=uuid.UUID(payload.order_id),
            buyer_id=uuid.UUID(payload.buyer_id),
            amount=payload.amount,
            currency=payload.currency,
            payment_method_type=PaymentMethodType.cash_on_delivery,
            status=PaymentStatus.pending,
        )
        db.add(tx)
        db.commit()

        # Immediately confirm COD orders — payment happens on delivery
        await confirm_order_with_service(payload.order_id, str(tx.id), None)

        return InitiatePaymentResponse(
            transaction_id=str(tx.id),
            payment_url=None,
            status=PaymentStatus.pending,
            message="Cash on delivery order confirmed"
        )

    # ── 3. Online payment — submit to PesaPal
    buyer   = await fetch_buyer_details(payload.buyer_id)
    ipn_id  = await get_ipn_id()

    # merchant_ref must be unique per order
    merchant_ref = f"SOKO-{payload.order_id[:8].upper()}"

    try:
        pesapal_res = await submit_order(
            merchant_ref=merchant_ref,
            amount=payload.amount,
            currency=payload.currency,
            description=payload.description,
            buyer_email=buyer.get("email", ""),
            buyer_phone=buyer.get("phone", ""),
            buyer_name=buyer.get("name", "Customer"),
            ipn_id=ipn_id,
            callback_url=f"{settings.PESAPAL_CALLBACK_URL}?order_id={payload.order_id}",
        )
    except Exception as e:
        logger.error(f"PesaPal submission failed for order {payload.order_id}: {e}")
        raise HTTPException(status_code=502, detail="Payment gateway unavailable")

    # ── 4. Save transaction record
    tx = Transaction(
        order_id=uuid.UUID(payload.order_id),
        buyer_id=uuid.UUID(payload.buyer_id),
        amount=payload.amount,
        currency=payload.currency,
        payment_method_type=PaymentMethodType(payload.payment_method.type),
        payment_provider=payload.payment_method.provider,
        payment_phone=payload.payment_method.phoneNumber,
        status=PaymentStatus.pending,
        pesapal_order_tracking_id=pesapal_res.get("order_tracking_id"),
        pesapal_merchant_ref=merchant_ref,
        pesapal_payment_url=pesapal_res.get("redirect_url"),
        pesapal_ipn_id=ipn_id,
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    return InitiatePaymentResponse(
        transaction_id=str(tx.id),
        payment_url=tx.pesapal_payment_url,
        status=PaymentStatus.pending,
        message="Redirect buyer to payment_url to complete payment"
    )


async def confirm_order_with_service(
    order_id:          str,
    transaction_id:    str,
    payment_reference: str | None
):
    """Tells Order Service that payment is confirmed."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.ORDER_SERVICE_URL}/internal/payment/confirmed",
                json={
                    "order_id":          order_id,
                    "payment_reference": payment_reference or transaction_id,
                    "paid_at":           datetime.utcnow().isoformat(),
                },
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
    except Exception as e:
        logger.error(f"Could not confirm order {order_id} with Order Service: {e}")


async def fail_order_with_service(order_id: str, reason: str):
    """Tells Order Service that payment failed."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.ORDER_SERVICE_URL}/internal/payment/failed",
                json={"order_id": order_id, "reason": reason},
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
    except Exception as e:
        logger.error(f"Could not fail order {order_id} with Order Service: {e}")