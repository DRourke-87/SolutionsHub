from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import Request
from itsdangerous import BadSignature, Signer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models import RateLimitCounter

SESSION_COOKIE = "sh_session"
ANON_COOKIE = "sh_anon"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"


def new_token() -> tuple[str, str]:
    """Return (raw_token_for_link, sha256_hash_for_storage)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _signer() -> Signer:
    return Signer(get_settings().secret_key, salt="solutionshub.session")


def sign_value(value: str) -> str:
    return _signer().sign(value.encode("utf-8")).decode("utf-8")


def unsign_value(signed: str | None) -> str | None:
    if not signed:
        return None
    try:
        return _signer().unsign(signed.encode("utf-8")).decode("utf-8")
    except BadSignature:
        return None


def client_ip(request: Request) -> str:
    """App Service terminates TLS and sets X-Forwarded-For; the edge IP allowlist means we can trust it."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip().split(":")[0]
    return request.client.host if request.client else "unknown"


def normalise_email(email: str) -> str:
    return email.strip().lower()


def email_domain(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""


def is_allowed_email(email: str) -> bool:
    email = normalise_email(email)
    if "@" not in email or email.count("@") != 1:
        return False
    local, domain = email.split("@")
    if not local or not domain:
        return False
    return domain in get_settings().allowed_domains


# --------------------------------------------------------------------------- CSRF
def csrf_token_for(identity: str) -> str:
    key = get_settings().secret_key.encode("utf-8")
    return hmac.new(key, f"csrf:{identity}".encode(), hashlib.sha256).hexdigest()


def csrf_identity(request: Request) -> str:
    """Identity the CSRF token is bound to: the session id if signed in, else the anonymous cookie."""
    sid = unsign_value(request.cookies.get(SESSION_COOKIE))
    if sid:
        return sid
    anon = unsign_value(request.cookies.get(ANON_COOKIE))
    if anon:
        return anon
    anon = getattr(request.state, "anon_id", None)
    if anon:
        return anon
    request.state.anon_id = secrets.token_urlsafe(24)
    return request.state.anon_id


def validate_csrf(request: Request, submitted: str | None) -> bool:
    if not submitted:
        return False
    expected = csrf_token_for(csrf_identity(request))
    return hmac.compare_digest(expected, submitted)


# --------------------------------------------------------------------------- rate limiting
def rate_limit_hit(db: Session, scope: str, key: str, limit: int, window_minutes: int = 60) -> bool:
    """Increment the counter for (scope, key) in the current window and return True if over the limit."""
    now = utcnow()
    window_start = now - timedelta(
        minutes=now.minute % window_minutes, seconds=now.second, microseconds=now.microsecond
    )
    row = db.execute(
        select(RateLimitCounter).where(
            RateLimitCounter.scope == scope,
            RateLimitCounter.key == key,
            RateLimitCounter.window_start == window_start,
        )
    ).scalar_one_or_none()
    if row is None:
        row = RateLimitCounter(scope=scope, key=key, window_start=window_start, count=0)
        db.add(row)
    row.count += 1
    db.flush()
    return row.count > limit
