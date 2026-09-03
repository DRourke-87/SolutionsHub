"""Notification queue. Rows are written inside the workflow transaction; delivery happens afterwards."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.enums import Role
from app.models import NotificationLog, Submission, User, UserRole
from app.services.email import get_email_backend

log = logging.getLogger("solutionshub.notify")

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "templates" / "emails")),
    autoescape=select_autoescape(["html"]),
)


def _render(template: str, **ctx) -> tuple[str, str, str]:
    """Return (subject, text, html) for a template name."""
    s = get_settings()
    ctx.setdefault("app_name", s.app_name)
    ctx.setdefault("base_url", s.base_url.rstrip("/"))
    subject = _env.get_template(f"{template}.subject.txt").render(**ctx).strip()
    text = _env.get_template(f"{template}.txt").render(**ctx)
    html = _env.get_template("base.html").render(subject=subject, body_template=f"{template}.html", **ctx)
    return subject, text, html


def queue(
    db: Session,
    template: str,
    recipients: set[str] | list[str],
    submission: Submission | None = None,
    exclude: set[str] | None = None,
    **ctx,
) -> list[NotificationLog]:
    s = get_settings()
    exclude = {e.lower() for e in (exclude or set())}
    rows: list[NotificationLog] = []
    if submission is not None:
        ctx.setdefault("sub", submission)
        ctx.setdefault("link", f"{s.base_url.rstrip('/')}/submissions/{submission.id}")
    for to in sorted({r.lower() for r in recipients if r} - exclude):
        subject, text, html = _render(template, to=to, **ctx)
        row = NotificationLog(
            submission_id=submission.id if submission is not None else None,
            to_email=to,
            template=template,
            subject=subject,
            body_text=text,
            body_html=html,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    return rows


def send_pending(db: Session, limit: int = 100) -> int:
    """Deliver queued notifications. Called after commit and by the scheduler for retries."""
    backend = get_email_backend()
    rows = (
        db.execute(
            select(NotificationLog)
            .where(NotificationLog.status == "queued", NotificationLog.attempts < 5)
            .order_by(NotificationLog.queued_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    sent = 0
    for row in rows:
        row.attempts += 1
        try:
            result = backend.send(row.to_email, row.subject, row.body_text, row.body_html)
            row.status = "sent"
            row.sent_at = utcnow()
            row.provider_message_id = result.provider_message_id
            sent += 1
        except Exception as exc:  # noqa: BLE001 - we record and retry later
            log.exception("email send failed to %s", row.to_email)
            row.error = str(exc)[:2000]
            if row.attempts >= 5:
                row.status = "failed"
        db.commit()
    return sent


# --------------------------------------------------------------------------- recipient resolution
def users_with_role(db: Session, role: Role, business_group_id: int | None = None) -> set[str]:
    q = (
        select(UserRole.email)
        .join(User, User.email == UserRole.email)
        .where(UserRole.role == role.value, UserRole.revoked_at.is_(None), User.is_disabled.is_(False))
    )
    if business_group_id is not None:
        q = q.where((UserRole.business_group_id.is_(None)) | (UserRole.business_group_id == business_group_id))
    return {e.lower() for e in db.execute(q).scalars()}


def reviewers_for(db: Session, sub: Submission) -> set[str]:
    return users_with_role(db, Role.REVIEWER, sub.business_group_id)


def approvers_for(db: Session, sub: Submission) -> set[str]:
    return users_with_role(db, Role.APPROVER, sub.business_group_id)


def publishers(db: Session) -> set[str]:
    return users_with_role(db, Role.PUBLISHER)


def admins(db: Session) -> set[str]:
    return users_with_role(db, Role.ADMIN)


def recently_notified(db: Session, submission_id: int, template: str, within: timedelta) -> bool:
    since = utcnow() - within
    row = db.execute(
        select(NotificationLog.id)
        .where(
            NotificationLog.submission_id == submission_id,
            NotificationLog.template == template,
            NotificationLog.queued_at >= since,
        )
        .limit(1)
    ).first()
    return row is not None
