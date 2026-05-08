import uuid
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import internal_only
from app.db.database import get_db
from app.helpers.builders import build_order_out
from app.models.order import Order, OrderStatus
from app.schemas.order import PaymentConfirmPayload, PaymentFailedPayload
from app.routers.orders import restore_stock, notify_order_event
from app.routers.orders import notify_order_event, update_buyer_stats

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Internal"], dependencies=[Depends(internal_only)])


@router.post("/payment/confirmed")
async def payment_confirmed(
    payload: PaymentConfirmPayload,
    db:      Session = Depends(get_db)
):
    """
    Called by Payment Service when PesaPal confirms payment.
    Moves order from pending → confirmed.
    """
    order = db.query(Order).filter(Order.id == uuid.UUID(payload.order_id)).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != OrderStatus.pending:
        logger.warning(f"Payment confirmed for non-pending order {order.id} ({order.status})")
        return {"message": "Order already processed"}

    order.status            = OrderStatus.confirmed
    order.payment_reference = payload.payment_reference
    order.paid_at           = datetime.fromisoformat(payload.paid_at)
    db.commit()

    # Notify buyer and farmer
    await notify_order_event(order, "payment_confirmed")
    await update_buyer_stats(str(order.buyer_id), order.total)

    return {"message": "Order confirmed", "order_id": str(order.id)}


@router.post("/payment/failed")
async def payment_failed(
    payload: PaymentFailedPayload,
    db:      Session = Depends(get_db)
):
    """
    Called by Payment Service when PesaPal payment fails or times out.
    Cancels the order and restores stock.
    """
    order = db.query(Order).filter(Order.id == uuid.UUID(payload.order_id)).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    if order.status != OrderStatus.pending:
        return {"message": "Order already processed"}

    order.status = OrderStatus.cancelled
    db.commit()

    # Restore stock for all items
    for item in order.items:
        await restore_stock(str(item.product_id), item.quantity)

    await notify_order_event(order, "payment_failed")
    logger.info(f"Order {order.id} cancelled — payment failed: {payload.reason}")
    return {"message": "Order cancelled", "order_id": str(order.id)}