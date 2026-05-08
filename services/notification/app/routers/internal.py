import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import httpx

from app.core.config import settings
from app.core.dependencies import internal_only
from app.db.database import get_db
from app.helpers.templates import get_template
from app.helpers.sms import send_sms
from app.helpers.push import push_to_user
from app.models.notification import Notification, NotificationType, NotificationChannel
from app.schemas.notification import NotifyPayload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Internal"], dependencies=[Depends(internal_only)])

SMS_EVENTS = {
    "order_placed",
    "payment_confirmed",
    "payment_failed",
    "order_dispatched",
    "system",
}


async def fetch_user(user_id: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{settings.USER_SERVICE_URL}/users/{user_id}",
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.warning(f"Could not fetch user {user_id}: {e}")
    return {}


async def deliver(
    db:        Session,
    user_id:   str,
    event:     str,
    role:      str,
    entity_id: str | None,
    meta:      dict,
    do_sms:    bool = False,
    phone:     str  = None,
):
    template = get_template(event, role, meta)
    if not template:
        logger.warning(f"No template for event={event} role={role}")
        return

    # ── In-app notification
    notif = Notification(
        user_id=uuid.UUID(user_id),
        type=NotificationType(event),
        channel=NotificationChannel.in_app,
        title=template.title,
        body=template.body,
        entity_type=template.entity_type,
        entity_id=entity_id,
        is_read=False,
        sent=True,
        sent_at=datetime.utcnow(),
    )
    db.add(notif)
    db.commit()

    # ── Real-time push
    await push_to_user(user_id, {
        "id":         str(notif.id),
        "type":       event,
        "title":      template.title,
        "body":       template.body,
        "entityType": template.entity_type,
        "entityId":   entity_id,
        "createdAt":  notif.created_at.isoformat(),
    })

    # ── SMS for important events
    if do_sms and phone and event in SMS_EVENTS:
        sms_sent = send_sms(phone, template.body)
        db.add(Notification(
            user_id=uuid.UUID(user_id),
            type=NotificationType(event),
            channel=NotificationChannel.sms,
            title=template.title,
            body=template.body,
            entity_type=template.entity_type,
            entity_id=entity_id,
            sent=sms_sent,
            sent_at=datetime.utcnow(),
        ))
        db.commit()


@router.post("/notify")
async def notify(payload: NotifyPayload, db: Session = Depends(get_db)):
    event = payload.event
    meta  = payload.meta or {}

    if payload.order_id:
        meta["order_ref"] = f"#{payload.order_id[:8].upper()}"

    # ── Buyer notifications
    if payload.buyer_id:
        buyer = await fetch_user(payload.buyer_id)
        await deliver(
            db=db,
            user_id=payload.buyer_id,
            event=event,
            role="buyer",
            entity_id=payload.order_id,
            meta=meta,
            do_sms=True,
            phone=buyer.get("phone"),
        )

    # ── Farmer notifications
    if payload.farmer_id and event in (
        "order_placed", "payment_confirmed",
        "order_cancelled", "new_review", "new_follower"
    ):
        await deliver(
            db=db,
            user_id=payload.farmer_id,
            event=event,
            role="farmer",
            entity_id=payload.order_id,
            meta=meta,
            do_sms=False,
        )

    # ── Direct message notification
    if event == "new_message" and payload.actor_id:
        meta["actor_name"] = payload.actor_name or "Someone"
        await deliver(
            db=db,
            user_id=payload.actor_id,
            event=event,
            role="recipient",
            entity_id=payload.message_id,
            meta=meta,
        )

    # ── System notification (used by USSD welcome SMS)
    if event == "system":
        target_id = payload.buyer_id or payload.farmer_id or payload.actor_id
        if target_id:
            user = await fetch_user(target_id)
            await deliver(
                db=db,
                user_id=target_id,
                event="system",
                role="user",
                entity_id=None,
                meta=meta,
                do_sms=True,
                phone=user.get("phone"),
            )

    return {"dispatched": True}