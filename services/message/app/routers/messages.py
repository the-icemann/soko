import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy import or_
from sqlalchemy.orm import Session
import httpx

from app.core.config import settings
from app.core.dependencies import get_current_user_id
from app.db.database import get_db
from app.helpers.builders import build_message_out
from app.helpers.connection_manager import (
    broadcast_to_conversation,
    send_to_user,
)
from app.models.messaging import Conversation, Message, MessageStatus
from app.schemas.schemas import MessageOut, SendMessagePayload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Messages"])


async def notify_new_message(
    recipient_id:  str,
    sender_name:   str,
    message_id:    str,
):
    """Tells Notification Service about a new message."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.NOTIFICATION_SERVICE_URL}/internal/notify",
                json={
                    "event":      "new_message",
                    "actor_id":   recipient_id,
                    "actor_name": sender_name,
                    "message_id": message_id,
                },
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=3.0,
            )
    except Exception as e:
        logger.warning(f"Notification failed for message {message_id}: {e}")


# ── Send a message
@router.post("/{conversation_id}/messages", response_model=MessageOut, status_code=201)
async def send_message(
    conversation_id: str,
    payload:         SendMessagePayload,
    user_id:         str     = Depends(get_current_user_id),
    db:              Session = Depends(get_db),
):
    conv = db.query(Conversation).filter(
        Conversation.id == uuid.UUID(conversation_id),
        or_(
            Conversation.buyer_id  == uuid.UUID(user_id),
            Conversation.farmer_id == uuid.UUID(user_id),
        )
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_buyer        = str(conv.buyer_id) == user_id
    sender_name     = conv.buyer_name     if is_buyer else conv.farmer_name
    sender_initials = conv.buyer_initials if is_buyer else conv.farmer_initials
    recipient_id    = str(conv.farmer_id) if is_buyer else str(conv.buyer_id)

    msg = Message(
        conversation_id=conv.id,
        sender_id=uuid.UUID(user_id),
        sender_name=sender_name,
        sender_initials=sender_initials,
        body=payload.body,
        status=MessageStatus.sent,
    )
    db.add(msg)

    # Update conversation summary
    conv.last_message    = payload.body
    conv.last_message_at = datetime.utcnow()
    conv.last_sender_id  = uuid.UUID(user_id)

    # Increment recipient unread count
    if is_buyer:
        conv.farmer_unread += 1
    else:
        conv.buyer_unread += 1

    db.commit()
    db.refresh(msg)

    result = build_message_out(msg, viewer_id=user_id)

    # ── Real-time delivery to recipient
    # Build a recipient-specific payload so isMine=False on their end
    recipient_result = build_message_out(msg, viewer_id=recipient_id)
    await broadcast_to_conversation(
        buyer_id=str(conv.buyer_id),
        farmer_id=str(conv.farmer_id),
        payload={
            "event": "new_message",
            "data":  recipient_result.model_dump(),
        },
        exclude=user_id,
    )

    # ── Push notification (only if recipient is offline)
    await notify_new_message(
        recipient_id=recipient_id,
        sender_name=sender_name,
        message_id=str(msg.id),
    )

    return result


# ── Delete (unsend) own message
@router.delete("/{conversation_id}/messages/{message_id}", status_code=204)
async def delete_message(
    conversation_id: str,
    message_id:      str,
    user_id:         str     = Depends(get_current_user_id),
    db:              Session = Depends(get_db),
):
    msg = db.query(Message).filter(
        Message.id              == uuid.UUID(message_id),
        Message.conversation_id == uuid.UUID(conversation_id),
        Message.sender_id       == uuid.UUID(user_id),
    ).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    msg.is_deleted = True
    msg.body       = ""
    db.commit()

    # Fetch conv to get both participant IDs for broadcast
    conv = db.query(Conversation).filter(
        Conversation.id == uuid.UUID(conversation_id)
    ).first()
    if conv:
        await broadcast_to_conversation(
            buyer_id=str(conv.buyer_id),
            farmer_id=str(conv.farmer_id),
            payload={
                "event": "message_deleted",
                "data": {
                    "messageId":      message_id,
                    "conversationId": conversation_id,
                },
            },
            exclude=user_id,
        )


# ── Mark a message as read
@router.post("/{conversation_id}/messages/{message_id}/read")
async def mark_message_read(
    conversation_id: str,
    message_id:      str,
    user_id:         str     = Depends(get_current_user_id),
    db:              Session = Depends(get_db),
):
    # Only the recipient can mark as read
    msg = db.query(Message).filter(
        Message.id              == uuid.UUID(message_id),
        Message.conversation_id == uuid.UUID(conversation_id),
        Message.sender_id       != uuid.UUID(user_id),
    ).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    msg.status = MessageStatus.read
    db.commit()

    # Notify sender their message was read
    await send_to_user(str(msg.sender_id), {
        "event": "message_read",
        "data": {
            "messageId":      message_id,
            "conversationId": conversation_id,
        },
    })

    return {"status": "read"}