import logging
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import AuthCredential, UserRole as DBUserRole
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_setup_token,
    decode_setup_token,
)
from app.core.config import settings
from app.schemas.auth import CompleteProfileRequest
from .auth import _sync_user_to_ml

logger = logging.getLogger(__name__)
router = APIRouter(tags=["OAuth"])

_GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_INFO_URL  = "https://www.googleapis.com/oauth2/v3/userinfo"


def _make_state() -> str:
    s = URLSafeTimedSerializer(settings.SECRET_KEY, salt="google-oauth-state")
    return s.dumps(secrets.token_hex(16))


def _verify_state(state: str) -> bool:
    s = URLSafeTimedSerializer(settings.SECRET_KEY, salt="google-oauth-state")
    try:
        s.loads(state, max_age=300)
        return True
    except (BadSignature, SignatureExpired):
        return False


@router.get("/google/login")
async def google_login():
    auth_url = _GOOGLE_AUTH_URL + "?" + urlencode({
        "client_id":     settings.GOOGLE_CLIENT_ID,
        "redirect_uri":  settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         _make_state(),
        "access_type":   "online",
    })
    return RedirectResponse(url=auth_url)


@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):

    # ── 1. Verify CSRF state (HMAC-signed, no storage needed)
    state = request.query_params.get("state")
    if not state or not _verify_state(state):
        logger.error("OAuth state invalid or expired")
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    error = request.query_params.get("error")
    if error:
        raise HTTPException(status_code=400, detail=f"Google OAuth error: {error}")

    code = request.query_params.get("code")
    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code")

    # ── 2. Exchange code for access token
    try:
        async with httpx.AsyncClient() as client:
            token_resp = await client.post(
                _GOOGLE_TOKEN_URL,
                data={
                    "code":          code,
                    "client_id":     settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri":  settings.GOOGLE_REDIRECT_URI,
                    "grant_type":    "authorization_code",
                },
                timeout=10.0,
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
    except Exception as e:
        logger.error(f"Google token exchange failed: {e}")
        raise HTTPException(status_code=400, detail="Google OAuth failed")

    # ── 3. Fetch user info
    try:
        async with httpx.AsyncClient() as client:
            info_resp = await client.get(
                _GOOGLE_INFO_URL,
                headers={"Authorization": f"Bearer {token_data['access_token']}"},
                timeout=10.0,
            )
            info_resp.raise_for_status()
            google_user = info_resp.json()
    except Exception as e:
        logger.error(f"Google userinfo fetch failed: {e}")
        raise HTTPException(status_code=400, detail="Could not fetch user info from Google")

    email      = google_user.get("email")
    name       = google_user.get("name") or email
    avatar_url = google_user.get("picture")

    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email address")

    # ── 4. Look up existing user
    user = db.query(AuthCredential).filter(AuthCredential.email == email).first()

    if user:
        if user.oauth_provider is None and user.hashed_password:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Please log in with your password.",
            )

        if user.is_profile_complete:
            access_token  = create_access_token(str(user.id), user.role.value, user.email)
            refresh_token = create_refresh_token(str(user.id))
            response = RedirectResponse(url=f"{settings.FRONTEND_URL}/home")
            _set_auth_cookies(response, access_token, refresh_token)
            return response

        # Returning user with incomplete profile — re-issue setup token
        setup_token = create_setup_token(
            user_id=str(user.id),
            email=user.email,
            name=name,
            avatar_url=avatar_url,
        )
        response = RedirectResponse(url=f"{settings.FRONTEND_URL}/auth/complete-profile")
        response.set_cookie(
            key="setup_token", value=setup_token,
            httponly=True, secure=True, samesite="lax", max_age=60 * 10,
        )
        return response

    # ── 5. New user — create skeleton credential
    user = AuthCredential(
        email=email,
        hashed_password=None,
        role=DBUserRole.buyer,
        oauth_provider="google",
        is_active=True,
        is_profile_complete=False,
    )
    db.add(user)
    db.flush()

    setup_token = create_setup_token(
        user_id=str(user.id),
        email=email,
        name=name,
        avatar_url=avatar_url,
    )

    db.commit()
    db.refresh(user)

    response = RedirectResponse(url=f"{settings.FRONTEND_URL}/auth/complete-profile")
    response.set_cookie(
        key="setup_token", value=setup_token,
        httponly=True, secure=True, samesite="lax", max_age=60 * 10,
    )
    return response


@router.post("/complete-profile")
async def complete_profile(
    body: CompleteProfileRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    setup_token: str | None = Cookie(default=None),
):
    if not setup_token:
        raise HTTPException(status_code=401, detail="Setup session missing or expired. Please sign in again.")

    payload = decode_setup_token(setup_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Setup session invalid or expired. Please sign in again.")

    user_id    = payload["sub"]
    email      = payload["email"]
    name       = payload["name"]
    avatar_url = payload.get("avatar_url")

    user = db.query(AuthCredential).filter(AuthCredential.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found. Please try signing in again.")

    if user.is_profile_complete:
        access_token  = create_access_token(str(user.id), user.role.value, user.email)
        refresh_token = create_refresh_token(str(user.id))
        response = JSONResponse({"message": "Profile already complete", "role": user.role.value})
        _set_auth_cookies(response, access_token, refresh_token)
        _clear_setup_cookie(response)
        return response

    user.role = body.role
    user.is_profile_complete = True
    db.flush()

    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{settings.USER_SERVICE_URL}/",
                json={
                    "id":          str(user.id),
                    "email":       email,
                    "role":        body.role.value,
                    "full_name":   name,
                    "phone":       body.phone,
                    "district":    body.district,
                    "avatar_url":  avatar_url,
                    "specialties": body.specialties,
                    "interests":   body.interests,
                },
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0,
            )
            res.raise_for_status()

        db.commit()
        db.refresh(user)

    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        logger.error(f"User Service profile creation failed: {e}")
        db.rollback()
        raise HTTPException(status_code=503, detail="Could not create user profile. Please try again.")

    access_token  = create_access_token(str(user.id), user.role.value, user.email)
    refresh_token = create_refresh_token(str(user.id))

    background_tasks.add_task(
        _sync_user_to_ml,
        str(user.id),
        body.role.value,
        name,
        body.district,
        body.specialties,
        body.interests,
    )

    response = JSONResponse({
        "message":      "Profile complete",
        "role":         user.role.value,
        "access_token": access_token,
    })
    _set_auth_cookies(response, access_token, refresh_token)
    _clear_setup_cookie(response)
    return response


# ── Helpers ──────────────────────────────────────────────────────────────────

def _set_auth_cookies(response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        key="access_token", value=access_token,
        httponly=True, secure=True, samesite="lax",
        max_age=60 * 60 * 24,
    )
    response.set_cookie(
        key="refresh_token", value=refresh_token,
        httponly=True, secure=True, samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )


def _clear_setup_cookie(response) -> None:
    response.delete_cookie(key="setup_token", httponly=True, secure=True, samesite="lax")
