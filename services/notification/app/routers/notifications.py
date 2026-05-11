import uuid
from fastapi import (
    APIRouter, Depends, Query,
    WebSocket, WebSocketDisconnect
)
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_current_user_id
from app.db.database import get_db
from app.helpers.push import register_connection, remove_connection
from app.models.notification import Notification, NotificationChannel
from app.schemas.notification import NotificationOut, MarkReadPayload

router = APIRouter(tags=["Notifications"])


def build_out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=str(n.id),
        type=n.type.value,
        channel=n.channel.value,
        title=n.title,
        body=n.body,
        entityType=n.entity_type,
        entityId=n.entity_id,
        isRead=n.is_read,
        createdAt=n.created_at.isoformat(),
    )


@router.get("/me", response_model=list[NotificationOut])
def get_notifications(
    unread_only: bool    = Query(default=False),
    page:        int     = Query(default=1,  ge=1),
    limit:       int     = Query(default=20, le=100),
    user_id:     str     = Depends(get_current_user_id),
    db:          Session = Depends(get_db)
):
    q = db.query(Notification).filter(
        Notification.user_id == uuid.UUID(user_id),
        Notification.channel == NotificationChannel.in_app,
    )
    if unread_only:
        q = q.filter(Notification.is_read == False)
    items = q.order_by(Notification.created_at.desc()) \
              .offset((page - 1) * limit).limit(limit).all()
    return [build_out(n) for n in items]


@router.get("/me/unread-count")
def unread_count(
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db)
):
    count = db.query(Notification).filter(
        Notification.user_id == uuid.UUID(user_id),
        Notification.channel == NotificationChannel.in_app,
        Notification.is_read == False,
    ).count()
    return {"unread": count}


@router.put("/me/read")
def mark_read(
    payload: MarkReadPayload,
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db)
):
    db.query(Notification).filter(
        Notification.id.in_([uuid.UUID(i) for i in payload.notification_ids]),
        Notification.user_id == uuid.UUID(user_id),
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"updated": len(payload.notification_ids)}


@router.put("/me/read-all")
def mark_all_read(
    user_id: str     = Depends(get_current_user_id),
    db:      Session = Depends(get_db)
):
    updated = db.query(Notification).filter(
        Notification.user_id == uuid.UUID(user_id),
        Notification.is_read == False,
    ).update({"is_read": True}, synchronize_session=False)
    db.commit()
    return {"updated": updated}


@router.websocket("/ws/{user_id}")
async def ws_notifications(
    websocket: WebSocket,
    user_id:   str,
    token:     str = Query(...),
):
    # Validate token before accepting the connection
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM]
        )
        if payload.get("sub") != user_id or payload.get("type") != "access":
            await websocket.close(code=4001)
            return
    except JWTError:
        await websocket.close(code=4001)
        return

    await websocket.accept()
    register_connection(user_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        remove_connection(user_id, websocket)