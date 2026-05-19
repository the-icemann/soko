import logging
from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from authlib.integrations.starlette_client import OAuth
from starlette.config import Config as StarletteConfig
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
import httpx

from .auth import _sync_user_to_ml

logger = logging.getLogger(__name__)

router = APIRouter(tags=["OAuth"])

starlette_config = StarletteConfig(environ={
    "GOOGLE_CLIENT_ID":     settings.GOOGLE_CLIENT_ID,
    "GOOGLE_CLIENT_SECRET": settings.GOOGLE_CLIENT_SECRET,
})
oauth = OAuth(starlette_config)
oauth.register(
    name="google",
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/google/login")
async def google_login(request: Request):
    return await oauth.google.authorize_redirect(
        request,
        settings.GOOGLE_REDIRECT_URI
    )


@router.get("/google/callback")
async def google_callback(request: Request, db: Session = Depends(get_db)):

    # ── 1. Exchange code for Google token
    try:
        google_token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        logger.error(f"Google OAuth token exchange failed: {e}")
        raise HTTPException(status_code=400, detail="Google OAuth failed")

    google_user = google_token.get("userinfo")
    if not google_user:
        raise HTTPException(status_code=400, detail="Could not fetch user info from Google")

    email      = google_user.get("email")
    name       = google_user.get("name") or email
    avatar_url = google_user.get("picture")

    if not email:
        raise HTTPException(status_code=400, detail="Google account has no email address")

    # ── 2. Check for existing user
    user = db.query(AuthCredential).filter(AuthCredential.email == email).first()

    if user:
        if user.oauth_provider is None and user.hashed_password:
            raise HTTPException(
                status_code=409,
                detail="An account with this email already exists. Please log in with your password."
            )

        # Returning OAuth user — issue real tokens immediately
        access_token  = create_access_token(str(user.id), user.role.value, user.email)
        refresh_token = create_refresh_token(str(user.id))
        response = RedirectResponse(url=f"{settings.FRONTEND_URL}/marketplace")
        _set_auth_cookies(response, access_token, refresh_token)
        return response

    # ── 3. New user — skeleton credential, no commit yet
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

    # ── 4. Issue short-lived setup token (carries Google data)
    setup_token = create_setup_token(
        user_id=str(user.id),
        email=email,
        name=name,
        avatar_url=avatar_url,
    )

    # ── 5. Commit skeleton, redirect to profile completion
    db.commit()
    db.refresh(user)

    response = RedirectResponse(url=f"{settings.FRONTEND_URL}/auth/complete-profile")
    response.set_cookie(
        key="setup_token",
        value=setup_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 10,
    )
    return response


@router.post("/complete-profile")
async def complete_profile(
    body: CompleteProfileRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    setup_token: str | None = Cookie(default=None),
):
    # ── 1. Validate setup token from HttpOnly cookie
    if not setup_token:
        raise HTTPException(status_code=401, detail="Setup session missing or expired. Please sign in again.")

    payload = decode_setup_token(setup_token)
    if not payload:
        raise HTTPException(status_code=401, detail="Setup session invalid or expired. Please sign in again.")

    user_id    = payload["sub"]
    email      = payload["email"]
    name       = payload["name"]
    avatar_url = payload.get("avatar_url")

    # ── 2. Load the skeleton credential
    user = db.query(AuthCredential).filter(AuthCredential.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found. Please try signing in again.")

    # ── 3. Guard against double submission
    if user.is_profile_complete:
        access_token  = create_access_token(str(user.id), user.role.value, user.email)
        refresh_token = create_refresh_token(str(user.id))
        response = JSONResponse({"message": "Profile already complete", "role": user.role.value})
        _set_auth_cookies(response, access_token, refresh_token)
        _clear_setup_cookie(response)
        return response

    # ── 4. Apply form data
    user.role = body.role
    user.is_profile_complete = True
    db.flush()

    # ── 5. Create full profile in User Service
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{settings.USER_SERVICE_URL}/users",
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

    # ── 6. Issue real tokens, clear setup cookie
    access_token  = create_access_token(str(user.id), user.role.value, user.email)
    refresh_token = create_refresh_token(str(user.id))

    # Sync new OAuth user to ML feature store so recommendations work immediately
    background_tasks.add_task(
        _sync_user_to_ml,
        str(user.id),
        body.role.value,
        name,
        body.district,
        body.specialties,
        body.interests,
    )

    response = JSONResponse({"message": "Profile complete", "role": user.role.value})
    _set_auth_cookies(response, access_token, refresh_token)
    _clear_setup_cookie(response)
    return response


# Helpers

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