import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session
import httpx

from app.core.config import settings
from app.core.dependencies import get_current_user_id
from app.db.database import get_db
from app.helpers.builders import build_conversation_out, build_message_out, make_initials
from app.models.messaging import Conversation, Message, MessageStatus
from app.schemas.schemas import ConversationOut, StartConversationPayload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Conversations"])


# ── Internal helpers ──────────────────────────────────────────────────────────

async def fetch_user(user_id: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{settings.USER_SERVICE_URL}/users/{user_id}",
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0,
            )
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.warning(f"Could not fetch user {user_id}: {e}")
    return {}


async def fetch_listing(listing_id: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"{settings.PRODUCE_SERVICE_URL}/listings/slug/{listing_id}",
                timeout=5.0,
            )
            if res.status_code == 200:
                return res.json()
    except Exception as e:
        logger.warning(f"Could not fetch listing {listing_id}: {e}")
    return {}


def _conversation_response(conv: Conversation, msg: Message, viewer_id: str, is_new: bool) -> dict[str, Any]:
    return {
        "conversation": build_conversation_out(conv, viewer_id=viewer_id).model_dump(),
        "message":      build_message_out(msg, viewer_id=viewer_id).model_dump(),
        "isNew":        is_new,
    }


# ── List conversations ────────────────────────────────────────────────────────

@router.get("", response_model=list[ConversationOut])
def get_conversations(
    page:    int     = Query(default=1,  ge=1),
    limit:   int     = Query(default=30, le=100),
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
):
    conversations = (
        db.query(Conversation)
        .filter(
            or_(
                Conversation.buyer_id  == uuid.UUID(user_id),
                Conversation.farmer_id == uuid.UUID(user_id),
            )
        )
        .order_by(Conversation.last_message_at.desc().nullslast())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )
    return [build_conversation_out(c, viewer_id=user_id) for c in conversations]


# ── Start or resume a conversation ───────────────────────────────────────────

@router.post("", response_model=dict, status_code=201)
async def start_conversation(
    payload: StartConversationPayload,
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
):
    initiator_id = uuid.UUID(user_id)
    recipient_id = uuid.UUID(payload.recipient_id)

    if initiator_id == recipient_id:
        raise HTTPException(status_code=400, detail="Cannot message yourself")

    # Resume existing conversation if one already exists (check both orderings)
    existing = db.query(Conversation).filter(
        or_(
            and_(
                Conversation.buyer_id  == initiator_id,
                Conversation.farmer_id == recipient_id,
            ),
            and_(
                Conversation.buyer_id  == recipient_id,
                Conversation.farmer_id == initiator_id,
            ),
        )
    ).first()

    if existing:
        is_buyer_slot = str(existing.buyer_id) == user_id
        msg = Message(
            conversation_id=existing.id,
            sender_id=initiator_id,
            sender_name=existing.buyer_name if is_buyer_slot else existing.farmer_name,
            sender_initials=existing.buyer_initials if is_buyer_slot else existing.farmer_initials,
            body=payload.first_message,
        )
        db.add(msg)
        existing.last_message    = payload.first_message
        existing.last_message_at = datetime.now(timezone.utc)
        existing.last_sender_id  = initiator_id
        if is_buyer_slot:
            existing.farmer_unread += 1
        else:
            existing.buyer_unread += 1
        db.commit()
        db.refresh(existing)
        return _conversation_response(existing, msg, viewer_id=user_id, is_new=False)

    # Fetch user snapshots in parallel
    buyer_id  = initiator_id
    farmer_id = recipient_id
    buyer, farmer = await asyncio.gather(
        fetch_user(user_id),
        fetch_user(payload.recipient_id),
    )

    listing_name = None
    if payload.listing_id:
        listing      = await fetch_listing(payload.listing_id)
        listing_name = listing.get("name")

    conv = Conversation(
        buyer_id=buyer_id,
        farmer_id=farmer_id,
        buyer_name=buyer.get("name", ""),
        buyer_initials=buyer.get("initials") or make_initials(buyer.get("name", "B")),
        buyer_avatar=buyer.get("avatarUrl"),
        farmer_name=farmer.get("name", ""),
        farmer_initials=farmer.get("initials") or make_initials(farmer.get("name", "F")),
        farmer_avatar=farmer.get("avatarUrl"),
        listing_id=uuid.UUID(payload.listing_id) if payload.listing_id else None,
        listing_name=listing_name,
        last_message=payload.first_message,
        last_message_at=datetime.now(timezone.utc),
        last_sender_id=buyer_id,
        farmer_unread=1,
    )
    db.add(conv)
    db.flush()

    msg = Message(
        conversation_id=conv.id,
        sender_id=buyer_id,
        sender_name=conv.buyer_name,
        sender_initials=conv.buyer_initials,
        body=payload.first_message,
    )
    db.add(msg)
    db.commit()
    db.refresh(conv)
    db.refresh(msg)

    return _conversation_response(conv, msg, viewer_id=user_id, is_new=True)


# ── Get single conversation with messages ─────────────────────────────────────

@router.get("/{conversation_id}", response_model=dict)
def get_conversation(
    conversation_id: str,
    page:    int     = Query(default=1,  ge=1),
    limit:   int     = Query(default=50, le=100),
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db),
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

    messages = (
        db.query(Message)
        .filter(Message.conversation_id == uuid.UUID(conversation_id))
        .order_by(Message.created_at.asc())
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    # Mark all incoming unread messages as read and reset unread counter
    is_buyer = str(conv.buyer_id) == user_id
    db.query(Message).filter(
        Message.conversation_id == uuid.UUID(conversation_id),
        Message.sender_id       != uuid.UUID(user_id),
        Message.status          != MessageStatus.read,
    ).update({"status": MessageStatus.read}, synchronize_session=False)

    if is_buyer:
        conv.buyer_unread = 0
    else:
        conv.farmer_unread = 0

    db.commit()

    return {
        "conversation": build_conversation_out(conv, viewer_id=user_id).model_dump(),
        "messages":     [build_message_out(m, viewer_id=user_id).model_dump() for m in messages],
    }