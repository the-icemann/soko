import json
import logging
from datetime import datetime

import africastalking
from fastapi import APIRouter, Depends, Form
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from app.core.config import settings

from app.core.dependencies import get_db
from app.models.ussd import USSDSession
from app.handlers.prices import handle_prices
from app.handlers.orders import handle_orders
from app.handlers.auth import handle_register, verify_login

africastalking.initialize(
    username=settings.AT_USERNAME,
    api_key=settings.AT_API_KEY,
)


logger = logging.getLogger(__name__)
router = APIRouter(tags=["USSD"])

MAIN_MENU = (
    "CON Welcome to Soko!\n"
    "1. Crop Prices\n"
    "2. My Orders\n"
    "3. Register\n"
    "0. Exit"
)

def get_or_create_session(
    session_id: str,
    phone:      str,
    db:         Session
):
    session = db.query(USSDSession).filter(
        USSDSession.session_id == session_id
    ).first()
    if not session:
        session = USSDSession(
            session_id=session_id,
            phone=phone,
            state="main_menu",
            data="{}",
            authenticated=False,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
    return session


def save_session(
    session:      USSDSession,
    next_state:   str,
    session_data: dict,
    authenticated: bool,
    db:           Session,
):
    session.state         = next_state
    session.data          = json.dumps(session_data)
    session.authenticated = authenticated
    session.updated_at    = datetime.utcnow()
    db.commit()


@router.post("/session", response_class=PlainTextResponse)
async def ussd_session(
    sessionId:   str = Form(...),
    serviceCode: str = Form(...),
    phoneNumber: str = Form(...),
    text:        str = Form(default=""),
    db: Session      = Depends(get_db)
):
    session      = get_or_create_session(sessionId, phoneNumber, db)
    state        = session.state
    session_data = json.loads(session.data or "{}")
    authenticated = session.authenticated
    user_input   = text.split("*")[-1] if text else ""

    logger.info(f"USSD: phone={phoneNumber} state={state} input='{user_input}'")

    response   = MAIN_MENU
    next_state = "main_menu"

    # ── Main menu
    if not text or state == "main_menu":
        if not user_input or user_input not in ("1", "2", "3", "0"):
            response   = MAIN_MENU
            next_state = "main_menu"

        elif user_input == "1":
            response, next_state, session_data = await handle_prices(
                "prices_district", user_input, session_data
            )

        elif user_input == "2":
            if not authenticated:
                response   = "CON Enter your 4-digit PIN:"
                next_state = "auth_pin_orders"
            else:
                platform_id = session_data.get("platform_id", "")
                response, next_state, session_data = await handle_orders(
                    "orders_list", "", session_data, platform_id
                )

        elif user_input == "3":
            response   = "CON Enter your full name:"
            next_state = "register_name"

        elif user_input == "0":
            response   = "END Thank you for using Soko!\nDial *384*1# anytime."
            next_state = "main_menu"

    # ── Crop price flow
    elif state in ("prices_district", "prices_category", "prices_result"):
        response, next_state, session_data = await handle_prices(
            state, user_input, session_data
        )

    # ── PIN verification before orders
    elif state == "auth_pin_orders":
        text_res, _, session_data, authed, platform_id = await verify_login(
            user_input, session_data, phoneNumber, db
        )
        if authed and platform_id:
            authenticated = True
            response, next_state, session_data = await handle_orders(
                "orders_list", "", session_data, platform_id
            )
        else:
            response   = text_res
            next_state = "main_menu"
            authenticated = False

    # ── Order detail flow
    elif state in ("orders_list", "orders_detail"):
        platform_id = session_data.get("platform_id", "")
        response, next_state, session_data = await handle_orders(
            state, user_input, session_data, platform_id
        )

    # ── Registration flow
    elif state in ("register_name", "register_pin", "register_role"):
        response, next_state, session_data, authed = await handle_register(
            state, user_input, session_data, phoneNumber, db
        )
        if authed:
            authenticated = True

    else:
        response   = MAIN_MENU
        next_state = "main_menu"

    save_session(session, next_state, session_data, authenticated, db)

    # PlainTextResponse — AT expects raw text, not JSON
    return response