"""Authentication / authorization helpers.

Staff (browser) auth: Supabase access token in `Authorization: Bearer ...`,
verified server-side with auth.get_user; staff id + role are derived from the
DB, never from the client.

Watcher auth: `X-Station-Key` header, compared by SHA-256 hash.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import httpx
import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, Request

from app.db import DatabaseUnreachable, get_settings, get_supabase, is_valid_uuid
from app.logic import hash_station_key

try:  # supabase-py >= 2.9 ships the auth client as supabase_auth, older as gotrue
    from supabase_auth.errors import AuthRetryableError
except ImportError:  # pragma: no cover
    try:
        from gotrue.errors import AuthRetryableError
    except ImportError:
        AuthRetryableError = None

log = logging.getLogger("ievaluate.auth")


def _is_network_error(exc: Exception) -> bool:
    """Supabase auth could not be reached (it wraps transport errors and
    502/503/504 replies in AuthRetryableError)."""
    if isinstance(exc, httpx.TransportError):
        return True
    return AuthRetryableError is not None and isinstance(exc, AuthRetryableError)

STAFF_ROLES = ("SAO_STAFF", "SAO_ADMIN")
INSTRUCTOR_ROLES = ("FULL-TIME", "PART-TIME", "DEPARTMENT_HEAD")

GENERIC_LOGIN_ERROR = "Invalid email or password, or this account is not allowed to use the scanning app."


@dataclass
class CurrentUser:
    id: str
    email: str | None
    first_name: str | None
    last_name: str | None
    role: str  # SAO_ADMIN | SAO_STAFF
    token: str = field(repr=False, default="")

    @property
    def is_admin(self) -> bool:
        return self.role == "SAO_ADMIN"

    @property
    def full_name(self) -> str:
        return " ".join(p for p in (self.first_name, self.last_name) if p) or (self.email or "")

    def public(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "role": self.role,
            "is_admin": self.is_admin,
        }


# ------------------------------------------------------------------ role lookup
_role_cache: dict = {"at": 0.0, "by_name": {}, "by_id": {}}
_role_lock = threading.Lock()


def role_maps() -> tuple[dict[str, int], dict[int, str]]:
    """roles table (tiny, static) cached for 10 minutes."""
    with _role_lock:
        if time.monotonic() - _role_cache["at"] > 600 or not _role_cache["by_name"]:
            rows = get_supabase().table("roles").select('id, "Roles"').execute().data or []
            by_name = {str(r["Roles"]).strip().upper(): int(r["id"]) for r in rows if r.get("Roles")}
            _role_cache.update(at=time.monotonic(), by_name=by_name, by_id={v: k for k, v in by_name.items()})
        return _role_cache["by_name"], _role_cache["by_id"]


def resolve_staff(user_id: str, email: str | None = None) -> CurrentUser | None:
    """Return the staff profile if the account may use this app, else None.
    Rules: user_info.account_status == 'approved', disabled not true, and a
    Sao_users row with role SAO_STAFF or SAO_ADMIN. (user_info.is_admin is
    intentionally ignored.)"""
    if not is_valid_uuid(user_id):
        return None
    sb = get_supabase()
    prof = (
        sb.table("user_info")
        .select("id, first_name, last_name, email, account_status, disabled")
        .eq("id", user_id)
        .limit(1)
        .execute()
    )
    if not prof.data:
        return None
    p = prof.data[0]
    if str(p.get("account_status") or "").strip().lower() != "approved" or p.get("disabled") is True:
        return None

    links = sb.table("Sao_users").select("role_id").eq("user_id", user_id).execute().data or []
    _, by_id = role_maps()
    names = {by_id.get(l.get("role_id")) for l in links}
    if "SAO_ADMIN" in names:
        role = "SAO_ADMIN"
    elif "SAO_STAFF" in names:
        role = "SAO_STAFF"
    else:
        return None
    return CurrentUser(
        id=user_id,
        email=p.get("email") or email,
        first_name=p.get("first_name"),
        last_name=p.get("last_name"),
        role=role,
    )


# ------------------------------------------------------------------ token cache
_TOKEN_TTL = 30.0
_token_cache: dict[str, tuple[float, CurrentUser]] = {}
_token_lock = threading.Lock()


def _token_key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _token_exp(token: str) -> float | None:
    try:
        claims = pyjwt.decode(token, options={"verify_signature": False})
        exp = claims.get("exp")
        return float(exp) if exp else None
    except Exception:
        return None


def forget_token(token: str) -> None:
    with _token_lock:
        _token_cache.pop(_token_key(token), None)


def verify_access_token(token: str) -> CurrentUser:
    key = _token_key(token)
    now = time.monotonic()
    with _token_lock:
        hit = _token_cache.get(key)
        if hit and hit[0] > now:
            return hit[1]

    exp = _token_exp(token)
    if exp is not None and exp <= time.time():
        raise HTTPException(status_code=401, detail={"code": "token_expired", "message": "Session expired. Please log in again."})

    try:
        res = get_supabase().auth.get_user(token)
        user = res.user if res else None
    except Exception as e:
        # A network problem is not an expired session: answer 503 so the
        # website keeps the user logged in and retries.
        if _is_network_error(e):
            raise DatabaseUnreachable(type(e).__name__) from e
        user = None
    if not user:
        raise HTTPException(status_code=401, detail={"code": "invalid_token", "message": "Session expired. Please log in again."})

    staff = resolve_staff(user.id, getattr(user, "email", None))
    if staff is None:
        raise HTTPException(status_code=401, detail={"code": "not_authorized", "message": "This account is not allowed to use the scanning app."})

    ttl = _TOKEN_TTL
    if exp is not None:
        ttl = max(1.0, min(ttl, exp - time.time()))
    with _token_lock:
        if len(_token_cache) > 2000:
            _token_cache.clear()
        _token_cache[key] = (now + ttl, staff)
    staff.token = token
    return staff


def require_staff(authorization: str | None = Header(default=None)) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "not_authenticated", "message": "Please log in."})
    token = authorization.split(" ", 1)[1].strip()
    if not token or len(token) > 8192:
        raise HTTPException(status_code=401, detail={"code": "not_authenticated", "message": "Please log in."})
    user = verify_access_token(token)
    # Return a copy that carries this request's token.
    return CurrentUser(user.id, user.email, user.first_name, user.last_name, user.role, token)


def require_admin(user: CurrentUser = Depends(require_staff)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail={"code": "admin_only", "message": "Only an SAO admin can do this."})
    return user


# ------------------------------------------------------------------ station key
def require_station(x_station_key: str | None = Header(default=None, alias="X-Station-Key")) -> dict:
    if not x_station_key or len(x_station_key) > 200:
        raise HTTPException(status_code=401, detail={"code": "invalid_station_key", "message": "Missing or invalid station key."})
    presented_hash = hash_station_key(x_station_key.strip())
    res = (
        get_supabase()
        .table("stations")
        .select("id, label, key_hash, folder_path, current_staff_id")
        .eq("key_hash", presented_hash)
        .limit(1)
        .execute()
    )
    row = res.data[0] if res.data else None
    if not row or not hmac.compare_digest(str(row.get("key_hash") or ""), presented_hash):
        raise HTTPException(status_code=401, detail={"code": "invalid_station_key", "message": "Missing or invalid station key."})
    row.pop("key_hash", None)
    return row


# ------------------------------------------------------------------ rate limit
class RateLimiter:
    """Tiny in-memory sliding-window limiter (per process)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 10000:
                self._hits = {k: v for k, v in self._hits.items() if v and v[-1] > now - window}
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


login_limiter = RateLimiter()


def _parse_ip(value: str | None) -> str | None:
    """Normalised IP string, or None when `value` is not a bare IPv4/IPv6 address."""
    if not value:
        return None
    candidate = value.strip()
    if candidate.startswith("[") and "]" in candidate:  # "[2001:db8::1]:443"
        candidate = candidate[1:candidate.index("]")]
    elif candidate.count(":") == 1:  # "203.0.113.5:1234"
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """The caller's IP for rate limiting.

    TRUST_PROXY_HEADERS=false (default, local runs): only the TCP peer
    (`request.client.host`), so nobody can pick their own rate-limit bucket by
    sending headers.

    TRUST_PROXY_HEADERS=true (Render, behind Cloudflare):
      1. CF-Connecting-IP, which Cloudflare always overwrites with the real client;
      2. else the RIGHTMOST valid X-Forwarded-For entry, i.e. the one appended by
         the nearest proxy (entries to its left are client-supplied and spoofable);
      3. else the TCP peer.
    """
    if get_settings().trust_proxy_headers:
        cf_ip = _parse_ip(request.headers.get("cf-connecting-ip"))
        if cf_ip:
            return cf_ip
        xff = ",".join(request.headers.getlist("x-forwarded-for"))
        for part in reversed(xff.split(",")):
            ip = _parse_ip(part)
            if ip:
                return ip
    return request.client.host if request.client else "unknown"
