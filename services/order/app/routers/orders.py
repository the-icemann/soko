import logging
import uuid
from typing import Optional
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
import httpx

from app.core.config import settings
from app.core.dependencies import get_current_user_id, buyer_only
from app.db.database import get_db
from app.helpers.builders import build_order_out, build_order_summary
from app.models.order import Order, OrderItem, OrderStatus
from app.schemas.order import (
    CheckoutPayload, OrderOut, OrderSummaryOut, UpdateOrderStatusPayload
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Orders"])

DELIVERY_FEE = 5000   # flat UGX — make dynamic later


async def verify_stock_and_get_details(items: list, db: Session) -> list:
    """
    Calls Produce Service to verify stock is available
    and fetches product snapshots for each item.
    Returns enriched item list or raises HTTPException.
    """
    enriched = []
    async with httpx.AsyncClient() as client:
        for item in items:
            try:
                res = await client.get(
                    f"{settings.PRODUCE_SERVICE_URL}/internal/listing/{item.productId}",
                    headers={"x-internal-secret": settings.INTERNAL_SECRET},
                    timeout=5.0
                )
                res.raise_for_status()
                product = res.json()
            except Exception as e:
                logger.error(f"Could not fetch product {item.productId}: {e}")
                raise HTTPException(
                    status_code=503,
                    detail=f"Could not verify product {item.productId}"
                )

            if product["status"] != "active":
                raise HTTPException(
                    status_code=400,
                    detail=f"'{product['name']}' is no longer available"
                )
            if product["qty"] < item.quantity:
                raise HTTPException(
                    status_code=400,
                    detail=f"Only {product['qty']} {product['unit']} of '{product['name']}' left"
                )
            if item.quantity < product["minimumOrder"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"Minimum order for '{product['name']}' is {product['minimumOrder']} {product['unit']}"
                )

            enriched.append({
                "item":    item,
                "product": product,
            })
    return enriched


async def decrement_stock(product_id: str, quantity: float):
    """Tells Produce Service to reduce available stock."""
    try:
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{settings.PRODUCE_SERVICE_URL}/internal/stock/decrement",
                json={"listing_id": product_id, "quantity": quantity},
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
    except Exception as e:
        logger.error(f"Stock decrement failed for {product_id}: {e}")


async def restore_stock(product_id: str, quantity: float):
    """Tells Produce Service to restore stock on cancellation."""
    try:
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{settings.PRODUCE_SERVICE_URL}/internal/stock/restore",
                json={"listing_id": product_id, "quantity": quantity},
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
    except Exception as e:
        logger.error(f"Stock restore failed for {product_id}: {e}")


async def notify_order_event(order: Order, event: str):
    """Tells Notification Service about an order event."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.NOTIFICATION_SERVICE_URL}/internal/notify",
                json={
                    "event":    event,
                    "order_id": str(order.id),
                    "buyer_id": str(order.buyer_id),
                    "status":   order.status.value,
                },
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
    except Exception as e:
        logger.warning(f"Notification failed for order {order.id}: {e}")


async def update_buyer_stats(buyer_id: str, total: float):
    """Tells User Service to increment buyer's order stats."""
    try:
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{settings.USER_SERVICE_URL}/users/{buyer_id}/stats/buyer",
                json={"total_orders": 1, "total_spent": int(total)},
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
    except Exception as e:
        logger.warning(f"Buyer stats update failed: {e}")


# ── Buyer only — place order (checkout)
@router.post("/", response_model=OrderOut, status_code=201,
             dependencies=[Depends(buyer_only)])
async def checkout(
    payload: CheckoutPayload,
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db)
):
    # ── 1. Verify all stock and fetch product snapshots
    enriched = await verify_stock_and_get_details(payload.items, db)

    # ── 2. Build order
    order = Order(
        buyer_id=uuid.UUID(user_id),
        status=OrderStatus.pending,
        subtotal=payload.totalAmount - DELIVERY_FEE,
        delivery_fee=DELIVERY_FEE,
        total=payload.totalAmount,
        currency=payload.currency,
        # Delivery address snapshot
        delivery_full_name=payload.deliveryAddress.fullName,
        delivery_phone=payload.deliveryAddress.phone,
        delivery_district=payload.deliveryAddress.district,
        delivery_sub_county=payload.deliveryAddress.subCounty,
        delivery_village=payload.deliveryAddress.village,
        delivery_landmark=payload.deliveryAddress.landmark,
        # Payment method snapshot
        payment_type=payload.paymentMethod.type,
        payment_provider=payload.paymentMethod.provider,
        payment_phone=payload.paymentMethod.phoneNumber,
        payment_account=payload.paymentMethod.accountName,
        # Estimated delivery — 2 days from now
        estimated_delivery=datetime.utcnow() + timedelta(days=2),
    )
    db.add(order)
    db.flush()   # get order.id before adding items

    # ── 3. Add order items as product snapshots
    for entry in enriched:
        item    = entry["item"]
        product = entry["product"]
        db.add(OrderItem(
            order_id=order.id,
            product_id=uuid.UUID(str(item.productId)),
            product_name=product["name"],
            product_image=product["image"],
            farmer_id=uuid.UUID(product["farmerId"]),
            farmer_name=product["farmer"],
            unit=product["unit"],
            category=product["category"],
            unit_price=item.unitPrice,
            quantity=item.quantity,
            subtotal=item.subtotal,
        ))

    db.commit()
    db.refresh(order)

    # ── 4. Decrement stock for all items
    for entry in enriched:
        await decrement_stock(str(entry["item"].productId), entry["item"].quantity)

    # ── 5. Initiate payment via Payment Service
    try:
        async with httpx.AsyncClient() as client:
            pay_res = await client.post(
                f"{settings.PAYMENT_SERVICE_URL}/internal/initiate",
                json={
                    "order_id":       str(order.id),
                    "buyer_id":       user_id,
                    "amount":         order.total,
                    "currency":       order.currency,
                    "payment_method": payload.paymentMethod.model_dump(),
                    "description":    f"Soko order {str(order.id)[:8]}",
                },
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=10.0
            )
            pay_res.raise_for_status()
            payment_data = pay_res.json()

    except Exception as e:
        logger.error(f"Payment initiation failed for order {order.id}: {e}")
        # Restore stock on payment failure
        for entry in enriched:
            await restore_stock(str(entry["item"].productId), entry["item"].quantity)
        # Cancel the order
        order.status = OrderStatus.cancelled
        db.commit()
        raise HTTPException(status_code=502, detail="Payment service unavailable. Please try again.")

    # ── 6. Notify buyer
    await notify_order_event(order, "order_placed")

    result = build_order_out(order)
    # Attach payment_url if returned — frontend redirects to PesaPal
    result_dict = result.model_dump()
    result_dict["paymentUrl"] = payment_data.get("payment_url")
    return result_dict


# ── Buyer — order history (paginated)
@router.get("/me", response_model=list[OrderSummaryOut])
def get_my_orders(
    status: Optional[str] = Query(default=None),
    page:   int           = Query(default=1,  ge=1),
    limit:  int           = Query(default=20, le=100),
    user_id: str          = Depends(get_current_user_id),
    db:      Session      = Depends(get_db)
):
    q = db.query(Order).filter(Order.buyer_id == uuid.UUID(user_id))
    if status:
        q = q.filter(Order.status == status)

    orders = q.order_by(Order.created_at.desc()) \
               .offset((page - 1) * limit).limit(limit).all()
    return [build_order_summary(o) for o in orders]


# ── Buyer — single order detail
@router.get("/me/{order_id}", response_model=OrderOut)
def get_my_order(
    order_id: str,
    user_id:  str     = Depends(get_current_user_id),
    db:       Session = Depends(get_db)
):
    order = db.query(Order).filter(
        Order.id       == uuid.UUID(order_id),
        Order.buyer_id == uuid.UUID(user_id)
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return build_order_out(order)


# ── Buyer — cancel order
@router.post("/me/{order_id}/cancel", response_model=OrderOut)
async def cancel_order(
    order_id: str,
    user_id:  str     = Depends(get_current_user_id),
    db:       Session = Depends(get_db)
):
    order = db.query(Order).filter(
        Order.id       == uuid.UUID(order_id),
        Order.buyer_id == uuid.UUID(user_id)
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Can only cancel pending or confirmed orders
    if order.status not in (OrderStatus.pending, OrderStatus.confirmed):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel an order that is already {order.status.value}"
        )

    order.status = OrderStatus.cancelled
    db.commit()

    # Restore stock for all items
    for item in order.items:
        await restore_stock(str(item.product_id), item.quantity)

    await notify_order_event(order, "order_cancelled")
    return build_order_out(order)


# ── Farmer — view orders for their produce (paginated)
@router.get("/farmer", response_model=list[OrderSummaryOut])
def get_farmer_orders(
    status:  Optional[str] = Query(default=None),
    page:    int           = Query(default=1,  ge=1),
    limit:   int           = Query(default=20, le=100),
    user_id: str           = Depends(get_current_user_id),
    db:      Session       = Depends(get_db)
):
    """Returns orders that contain at least one item from this farmer."""
    q = db.query(Order).join(Order.items).filter(
        OrderItem.farmer_id == uuid.UUID(user_id)
    )
    if status:
        q = q.filter(Order.status == status)

    orders = q.distinct().order_by(Order.created_at.desc()) \
               .offset((page - 1) * limit).limit(limit).all()
    return [build_order_summary(o) for o in orders]


# ── Farmer — update order status (e.g. mark dispatched)
@router.put("/farmer/{order_id}/status", response_model=OrderOut)
async def update_order_status(
    order_id: str,
    payload:  UpdateOrderStatusPayload,
    user_id:  str     = Depends(get_current_user_id),
    db:       Session = Depends(get_db)
):
    order = db.query(Order).join(Order.items).filter(
        Order.id            == uuid.UUID(order_id),
        OrderItem.farmer_id == uuid.UUID(user_id)
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    # Farmers can only move orders forward — not backwards or cancel
    allowed_transitions = {
        OrderStatus.confirmed:  [OrderStatus.processing],
        OrderStatus.processing: [OrderStatus.dispatched],
        OrderStatus.dispatched: [OrderStatus.delivered],
    }
    allowed = allowed_transitions.get(order.status, [])
    if payload.status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot move order from {order.status.value} to {payload.status.value}"
        )

    order.status = payload.status
    db.commit()

    await notify_order_event(order, f"order_{payload.status.value}")
    return build_order_out(order)