import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, Header
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.user import AuthCredential, UserRole as DBUserRole
from app.core.config import settings
from app.schemas.auth import (
    RegisterPayload, LoginPayload, LoginResponse,
    AuthTokens, AuthUserMinimal,
    VerifyTokenRequest, VerifyTokenResponse,
    ChangePasswordPayload
)
from app.core.security import (
    hash_password, verify_password,
    create_access_token, create_refresh_token, decode_token
)
from app.core.dependencies import get_current_user
import httpx

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Auth"])


async def _sync_user_to_ml(
    user_id:     str,
    role:        str,
    full_name:   str,
    district:    str | None,
    specialties: list | None,
    interests:   list | None,
) -> None:
    """Fire-and-forget: sync new user into the ML feature store immediately."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{settings.INGEST_SERVICE_URL}/ingest/user-created",
                json={
                    "id":          user_id,
                    "role":        role,
                    "full_name":   full_name,
                    "district":    district,
                    "specialties": specialties,
                    "interests":   interests,
                },
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=8.0,
            )
    except Exception as e:
        logger.warning(f"ML feature store sync failed for user {user_id}: {e}")


@router.post("/register", response_model=LoginResponse, status_code=201)
async def register(payload: RegisterPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):

    # ── 1. Check for existing email
    if db.query(AuthCredential).filter(AuthCredential.email == payload.email).first():
        raise HTTPException(status_code=409, detail="Email already registered")

    # ── 2. Create credentials but don't commit yet
    cred = AuthCredential(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=DBUserRole(payload.role),
        oauth_provider=None,
        is_active=True,
    )
    db.add(cred)
    db.flush()   # assigns cred.id without committing

    # ── 3. Create profile in User Service — rollback if it fails
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{settings.USER_SERVICE_URL}/",
                json={
                    "id":          str(cred.id),
                    "email":       cred.email,
                    "role":        cred.role.value,
                    "full_name":   payload.fullName,
                    "phone":       payload.phone or None,
                    "district":    payload.district or None,
                    "avatar_url":  payload.avatar_url or None,
                    "specialties": payload.specialties or None,
                    "interests":   payload.interests or None,
                },
                headers={"x-internal-secret": settings.INTERNAL_SECRET},
                timeout=5.0
            )
            res.raise_for_status()

        db.commit()
        db.refresh(cred)

    except httpx.HTTPStatusError as e:
        db.rollback()
        try:
            detail = e.response.json().get("detail", "Could not create user profile")
        except Exception:
            detail = "Could not create user profile"
        status_code = 409 if e.response.status_code == 409 else 503
        raise HTTPException(status_code=status_code, detail=detail)
    except httpx.RequestError as e:
        logger.error(f"User service unreachable: {e}")
        db.rollback()
        raise HTTPException(status_code=503, detail="Could not reach user service")


    # ── 4. Issue tokens
    access_token  = create_access_token(str(cred.id), cred.role.value, cred.email)
    refresh_token = create_refresh_token(str(cred.id))

    # Sync new user to ML feature store so recommendations are available immediately
    background_tasks.add_task(
        _sync_user_to_ml,
        str(cred.id),
        cred.role.value,
        payload.fullName,
        payload.district,
        payload.specialties,
        payload.interests,
    )

    return LoginResponse(
        tokens=AuthTokens(access_token=access_token, refresh_token=refresh_token),
        user=AuthUserMinimal(id=str(cred.id), email=cred.email, role=cred.role.value)
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginPayload, db: Session = Depends(get_db)):
    cred = db.query(AuthCredential).filter(AuthCredential.email == payload.email).first()

    # ── Block OAuth users from password login
    if cred and cred.oauth_provider:
        raise HTTPException(
            status_code=400,
            detail=f"This account uses {cred.oauth_provider.title()} sign-in. Please use that instead."
        )

    if not cred or not verify_password(payload.password, cred.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if not cred.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    access_token  = create_access_token(str(cred.id), cred.role.value, cred.email)
    refresh_token = create_refresh_token(str(cred.id))

    return LoginResponse(
        tokens=AuthTokens(access_token=access_token, refresh_token=refresh_token),
        user=AuthUserMinimal(id=str(cred.id), email=cred.email, role=cred.role.value)
    )


@router.get("/verify-token")
def verify_token_gateway(
    response: Response,
    authorization: str = Header(...)
):
    """Called by nginx auth_request — verifies JWT and injects user headers."""
    token = authorization.replace("Bearer ", "")
    data = decode_token(token, token_type="access")
    if not data:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    response.headers["x-user-id"]    = data["sub"]
    response.headers["x-user-role"]  = data["role"]
    response.headers["x-user-email"] = data["email"]

    return {"valid": True}


@router.post("/refresh", response_model=AuthTokens)
def refresh(payload: VerifyTokenRequest, db: Session = Depends(get_db)):
    """Issues a new access token from a valid refresh token."""

    # ── Must be a refresh token specifically — not an access token
    data = decode_token(payload.token, token_type="refresh")
    if not data:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = db.query(AuthCredential).filter(
        AuthCredential.id == data["sub"]
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    return AuthTokens(
        access_token=create_access_token(str(user.id), user.role.value, user.email),
        refresh_token=create_refresh_token(str(user.id))
    )


@router.post("/change-password")
def change_password(
    payload: ChangePasswordPayload,
    current_user: AuthCredential = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    # ── OAuth users have no password
    if current_user.oauth_provider:
        raise HTTPException(
            status_code=400,
            detail="OAuth accounts don't have a password. Use your Google account to sign in."
        )

    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    current_user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"message": "Password updated"}


@router.post("/logout")
def logout():
    """
    With stateless JWT the client just drops the token.
    If you add a Redis blacklist later, invalidate the token here.
    """
    return {"message": "Logged out successfully"}


@router.get("/health")
def health():
    return {"status": "ok", "service": "auth"}

@router.get("/verify-token-optional")
def verify_token_optional(
    response: Response,
    authorization: str = Header(default=None)
):
    """Like verify-token but returns 200 even without a token.
    Used by nginx for routes that are public for GET but protected for writes."""
    if authorization:
        token = authorization.replace("Bearer ", "")
        data = decode_token(token, token_type="access")
        if data:
            response.headers["x-user-id"]    = data["sub"]
            response.headers["x-user-role"]  = data["role"]
            response.headers["x-user-email"] = data["email"]
    return {"valid": True}