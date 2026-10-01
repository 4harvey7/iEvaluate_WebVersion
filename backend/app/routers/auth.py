import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import get_supabase, new_auth_client, is_valid_uuid
from app.security import (
    GENERIC_LOGIN_ERROR,
    CurrentUser,
    client_ip,
    forget_token,
    login_limiter,
    require_staff,
    resolve_staff,
)

router = APIRouter()
log = logging.getLogger("ievaluate.auth")


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=512)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4096)


class LogoutRequest(BaseModel):
    station_id: str | None = None


def _session_payload(session, staff: CurrentUser) -> dict:
    return {
        "access_token": session.access_token,
        "refresh_token": session.refresh_token,
        "expires_at": session.expires_at,
        "expires_in": session.expires_in,
        "profile": staff.public(),
    }


def _revoke(client) -> None:
    try:
        client.auth.sign_out()
    except Exception:
        pass


@router.post("/login")
def login(req: LoginRequest, request: Request):
    email = req.email.strip().lower()
    ip = client_ip(request)
    if not login_limiter.hit(f"ip:{ip}", 30, 300) or not login_limiter.hit(f"email:{email}", 8, 300):
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "Too many login attempts. Wait a few minutes and try again."})

    client = new_auth_client()
    try:
        result = client.auth.sign_in_with_password({"email": email, "password": req.password})
    except Exception:
        raise HTTPException(status_code=401, detail={"code": "login_failed", "message": GENERIC_LOGIN_ERROR})

    user = result.user if result else None
    session = result.session if result else None
    if not user or not session:
        raise HTTPException(status_code=401, detail={"code": "login_failed", "message": GENERIC_LOGIN_ERROR})

    staff = resolve_staff(user.id, user.email)
    if staff is None:
        # Valid password but not an approved SAO account: revoke the session we
        # just created and answer exactly like a bad password.
        log.info("login refused for non-SAO/unapproved account")
        _revoke(client)
        raise HTTPException(status_code=401, detail={"code": "login_failed", "message": GENERIC_LOGIN_ERROR})

    login_limiter.reset(f"email:{email}")
    return _session_payload(session, staff)


@router.post("/refresh")
def refresh(req: RefreshRequest, request: Request):
    if not login_limiter.hit(f"refresh:{client_ip(request)}", 120, 300):
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": "Too many requests."})
    client = new_auth_client()
    try:
        result = client.auth.refresh_session(req.refresh_token)
    except Exception:
        raise HTTPException(status_code=401, detail={"code": "refresh_failed", "message": "Session expired. Please log in again."})
    session = result.session if result else None
    user = result.user if result else None
    if not session or not user:
        raise HTTPException(status_code=401, detail={"code": "refresh_failed", "message": "Session expired. Please log in again."})
    # Re-check authorization: the account may have been disabled / demoted.
    staff = resolve_staff(user.id, user.email)
    if staff is None:
        _revoke(client)
        raise HTTPException(status_code=401, detail={"code": "not_authorized", "message": "This account is not allowed to use the scanning app."})
    return _session_payload(session, staff)


@router.get("/me")
def me(user: CurrentUser = Depends(require_staff)):
    return user.public()


@router.post("/logout")
def logout(req: LogoutRequest | None = None, user: CurrentUser = Depends(require_staff)):
    sb = get_supabase()
    station_id = req.station_id if req else None
    if station_id and is_valid_uuid(station_id):
        # Only unbind if this user is the one bound to the station.
        sb.table("stations").update({
            "current_staff_id": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", station_id).eq("current_staff_id", user.id).execute()
    try:
        # Revokes this user's refresh tokens (access token expires on its own).
        sb.auth.admin.sign_out(user.token, "local")
    except Exception:
        pass
    forget_token(user.token)
    return {"ok": True}


