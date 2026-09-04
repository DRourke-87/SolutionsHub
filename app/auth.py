from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.db import get_db, utcnow
from app.enums import EventType, Role
from app.models import MagicLinkToken, User, UserRole, UserSession, WorkflowEvent
from app.security import (
    SESSION_COOKIE,
    client_ip,
    email_domain,
    hash_token,
    is_allowed_email,
    new_token,
    normalise_email,
    rate_limit_hit,
    sign_value,
    unsign_value,
)


class LoginError(Exception):
    pass


class RateLimited(LoginError):
    pass


def _is_expired(expires_at, now) -> bool:
    if expires_at is None:
        return True
    if getattr(expires_at, "tzinfo", None) is None and getattr(now, "tzinfo", None) is not None:
        now = now.replace(tzinfo=None)
    return expires_at < now


def _seconds_between(later, earlier) -> int:
    if getattr(later, "tzinfo", None) is None and getattr(earlier, "tzinfo", None) is not None:
        earlier = earlier.replace(tzinfo=None)
    return int((later - earlier).total_seconds())


def _elapsed_seconds(later, earlier) -> int:
    if getattr(earlier, "tzinfo", None) is None and getattr(later, "tzinfo", None) is not None:
        later = later.replace(tzinfo=None)
    return int((later - earlier).total_seconds())


def safe_next_path(path: str | None) -> str:
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path


# --------------------------------------------------------------------------- magic link
def request_magic_link(
    db: Session, email: str, ip: str, next_path: str = "/", remember_me: bool = False
) -> tuple[str | None, str | None]:
    """Create a token for the email if allowed. Returns (raw_token, link) or (None, None).

    The caller must respond identically whether or not a token was created.
    """
    settings = get_settings()
    email = normalise_email(email)
    if rate_limit_hit(db, "login_ip", ip, settings.login_rate_per_ip_per_hour):
        raise RateLimited("Too many sign-in requests from this network. Please wait and try again.")
    if not is_allowed_email(email):
        return None, None
    if rate_limit_hit(db, "login_email", email, settings.login_rate_per_email_per_hour):
        raise RateLimited("Too many sign-in requests for this address. Please wait and try again.")

    user = db.get(User, email)
    if user and user.is_disabled:
        return None, None

    # Invalidate earlier unused tokens for this address
    now = utcnow()
    for old in db.execute(
        select(MagicLinkToken).where(MagicLinkToken.email == email, MagicLinkToken.used_at.is_(None))
    ).scalars():
        old.used_at = now

    raw, token_hash = new_token()
    db.add(
        MagicLinkToken(
            token_hash=token_hash,
            email=email,
            redirect_path=safe_next_path(next_path),
            remember_me=remember_me,
            expires_at=now + timedelta(minutes=settings.magic_link_ttl_minutes),
            request_ip=ip,
        )
    )
    db.flush()
    link = f"{settings.base_url.rstrip('/')}/auth/verify?t={quote(raw)}"
    return raw, link


def verify_magic_link(db: Session, raw_token: str, ip: str, user_agent: str | None) -> tuple[UserSession, str]:
    """Consume a token and open a session. Returns (session, redirect_path). Raises LoginError."""
    settings = get_settings()
    if rate_limit_hit(db, "verify_ip", ip, settings.verify_failures_per_ip_per_hour):
        raise RateLimited("Too many sign-in attempts. Please wait and try again.")
    token = db.get(MagicLinkToken, hash_token(raw_token or ""))
    now = utcnow()
    if token is None or token.used_at is not None or _is_expired(token.expires_at, now):
        raise LoginError("This sign-in link is invalid or has expired. Please request a new one.")
    token.used_at = now

    user = db.get(User, token.email)
    if user is None:
        user = User(email=token.email, domain=email_domain(token.email), first_seen_at=now)
        db.add(user)
    if user.is_disabled:
        raise LoginError("This account has been disabled. Contact a SolutionsHub administrator.")
    user.last_sign_in_at = now
    db.flush()
    _maybe_bootstrap_admin(db, user)

    if token.remember_me:
        sliding = timedelta(days=settings.remember_me_days)
        absolute = timedelta(days=settings.remember_me_days)
    else:
        sliding = timedelta(hours=settings.session_sliding_hours)
        absolute = timedelta(hours=settings.session_absolute_hours)
    session = UserSession(
        id=secrets.token_urlsafe(32),
        email=user.email,
        created_at=now,
        last_seen_at=now,
        expires_at=now + sliding,
        absolute_expires_at=now + absolute,
        ip=ip,
        user_agent=(user_agent or "")[:400],
    )
    db.add(session)
    db.add(
        WorkflowEvent(
            event_type=EventType.SIGN_IN.value,
            actor_email=user.email,
            actor_name=user.display_name,
            ip=ip,
            user_agent=(user_agent or "")[:400],
            note="magic link",
        )
    )
    db.flush()
    return session, safe_next_path(token.redirect_path)


def magic_link_error(db: Session, raw_token: str) -> str | None:
    token = db.get(MagicLinkToken, hash_token(raw_token or ""))
    now = utcnow()
    if token is None or token.used_at is not None or _is_expired(token.expires_at, now):
        return "This sign-in link is invalid or has expired. Please request a new one."
    return None


def _maybe_bootstrap_admin(db: Session, user: User) -> None:
    bootstrap = get_settings().bootstrap_admin_email
    if not bootstrap or normalise_email(bootstrap) != user.email:
        return
    has_admin = db.execute(
        select(UserRole.id).where(UserRole.role == Role.ADMIN.value, UserRole.revoked_at.is_(None)).limit(1)
    ).first()
    if has_admin:
        return
    db.add(UserRole(email=user.email, role=Role.ADMIN.value, granted_by_email="bootstrap"))
    db.add(
        WorkflowEvent(
            event_type=EventType.ROLE_GRANTED.value,
            actor_email="bootstrap",
            note=f"admin granted to {user.email} via BOOTSTRAP_ADMIN_EMAIL",
        )
    )
    db.flush()


# --------------------------------------------------------------------------- sessions
def set_session_cookie(response, session: UserSession) -> None:
    settings = get_settings()
    max_age = _seconds_between(session.absolute_expires_at, utcnow())
    response.set_cookie(
        SESSION_COOKIE,
        sign_value(session.id),
        max_age=max(max_age, 60),
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def load_session(db: Session, request: Request) -> tuple[UserSession | None, User | None]:
    sid = unsign_value(request.cookies.get(SESSION_COOKIE))
    if not sid:
        return None, None
    session = db.get(UserSession, sid)
    now = utcnow()
    if session is None or session.revoked_at is not None:
        return None, None
    if _is_expired(session.expires_at, now) or _is_expired(session.absolute_expires_at, now):
        return None, None
    user = db.execute(
        select(User)
        .options(selectinload(User.roles).selectinload(UserRole.business_group))
        .where(User.email == session.email)
    ).scalar_one_or_none()
    if user is None or user.is_disabled:
        return None, None
    # Sliding expiry, throttled to one write per 5 minutes
    if _elapsed_seconds(now, session.last_seen_at) > 300:
        settings = get_settings()
        session.last_seen_at = now
        session.expires_at = min(now + timedelta(hours=settings.session_sliding_hours), session.absolute_expires_at)
        db.commit()
    return session, user


def revoke_session(db: Session, session_id: str | None) -> None:
    if not session_id:
        return
    session = db.get(UserSession, session_id)
    if session:
        session.revoked_at = utcnow()


def revoke_all_sessions(db: Session, email: str) -> None:
    now = utcnow()
    for s in db.execute(
        select(UserSession).where(UserSession.email == email, UserSession.revoked_at.is_(None))
    ).scalars():
        s.revoked_at = now


# --------------------------------------------------------------------------- dependencies
def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    session, user = load_session(db, request)
    request.state.user = user
    request.state.session = session
    return user


def require_user(request: Request, user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        target = request.url.path
        if request.url.query:
            target += "?" + request.url.query
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": f"/login?next={quote(target, safe='')}"},
        )
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user


def login_redirect(next_path: str) -> RedirectResponse:
    return RedirectResponse(f"/login?next={quote(next_path, safe='')}", status_code=303)


__all__ = [
    "LoginError",
    "RateLimited",
    "client_ip",
    "request_magic_link",
    "magic_link_error",
    "verify_magic_link",
    "set_session_cookie",
    "clear_session_cookie",
    "load_session",
    "revoke_session",
    "revoke_all_sessions",
    "get_current_user",
    "require_user",
    "require_admin",
    "login_redirect",
    "safe_next_path",
]
