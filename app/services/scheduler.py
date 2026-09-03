"""Background jobs: notification delivery, action reminders, six-month review cycle, housekeeping.

Runs in-process (APScheduler) on the single App Service instance. A PostgreSQL advisory lock makes the
jobs safe if the plan is ever scaled to more than one instance.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_engine, get_sessionmaker, utcnow
from app.enums import EventType, Status
from app.models import (
    MagicLinkToken,
    RateLimitCounter,
    ReviewCycle,
    Submission,
    UserSession,
    WorkflowEvent,
)
from app.services import notifications as notify

log = logging.getLogger("solutionshub.scheduler")
LOCK_KEY = 7345_2026


def business_days_between(start: datetime, end: datetime) -> int:
    if end <= start:
        return 0
    days = 0
    cur = start.date()
    while cur < end.date():
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            days += 1
    return days


def _locked(fn: Callable[[Session], None]) -> Callable[[], None]:
    def run() -> None:
        engine = get_engine()
        lock_conn = None
        try:
            if engine.dialect.name == "postgresql":
                lock_conn = engine.connect()
                got = lock_conn.execute(text("select pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar()
                if not got:
                    return
            with get_sessionmaker()() as db:
                fn(db)
        except Exception:  # noqa: BLE001
            log.exception("scheduled job %s failed", fn.__name__)
        finally:
            if lock_conn is not None:
                try:
                    lock_conn.execute(text("select pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
                finally:
                    lock_conn.close()

    run.__name__ = fn.__name__
    return run


# --------------------------------------------------------------------------- jobs
def deliver_notifications(db: Session) -> None:
    notify.send_pending(db)


def _reminder_count(db: Session, sub: Submission) -> int:
    return len(
        db.execute(
            select(WorkflowEvent.id).where(
                WorkflowEvent.submission_id == sub.id,
                WorkflowEvent.event_type == EventType.REMINDER_SENT.value,
                WorkflowEvent.created_at >= sub.last_action_at,
            )
        ).all()
    )


def send_action_reminders(db: Session) -> None:
    """Nudge whoever owns the next action once it has been outstanding beyond the configured threshold."""
    s = get_settings()
    now = utcnow()
    if now.weekday() >= 5:
        return
    plan = {
        Status.SUBMITTED.value: (s.reminder_reviewer_pool_days, "reviewer_pool"),
        Status.UNDER_REVIEW.value: (s.reminder_assigned_reviewer_days, "reviewer"),
        Status.UPDATES_REQUIRED.value: (s.reminder_owner_days, "owners"),
        Status.AWAITING_APPROVAL.value: (s.reminder_approver_days, "approvers"),
        Status.APPROVED.value: (s.reminder_publisher_days, "publishers"),
        Status.READY_TO_PUBLISH.value: (s.reminder_publisher_days, "publishers"),
    }
    subs = (
        db.execute(select(Submission).where(Submission.status.in_(list(plan)), Submission.archived_at.is_(None)))
        .scalars()
        .all()
    )
    for sub in subs:
        threshold, audience = plan[sub.status]
        if business_days_between(sub.last_action_at, now) < threshold:
            continue
        last_reminder = db.execute(
            select(WorkflowEvent.created_at)
            .where(
                WorkflowEvent.submission_id == sub.id,
                WorkflowEvent.event_type == EventType.REMINDER_SENT.value,
            )
            .order_by(WorkflowEvent.created_at.desc())
            .limit(1)
        ).scalar()
        if last_reminder and business_days_between(last_reminder, now) < threshold:
            continue
        count = _reminder_count(db, sub)
        if audience == "reviewer_pool":
            recipients = notify.reviewers_for(db, sub)
            cc = notify.admins(db) if count >= 2 else set()
        elif audience == "reviewer":
            recipients = {sub.assigned_reviewer_email} if sub.assigned_reviewer_email else notify.reviewers_for(db, sub)
            cc = notify.admins(db) if count >= 2 else set()
        elif audience == "owners":
            recipients = set(sub.owner_emails)
            cc = {sub.assigned_reviewer_email} if count >= 3 and sub.assigned_reviewer_email else set()
        elif audience == "approvers":
            recipients = notify.approvers_for(db, sub)
            cc = notify.admins(db) if count >= 2 else set()
        else:
            recipients = notify.publishers(db)
            cc = notify.admins(db) if count >= 2 else set()
        if not recipients and not cc:
            continue
        days = business_days_between(sub.last_action_at, now)
        notify.queue(db, "action_reminder", recipients | cc, sub, days=days, waiting_on=audience.replace("_", " "))
        db.add(
            WorkflowEvent(
                submission_id=sub.id,
                revision=sub.revision,
                event_type=EventType.REMINDER_SENT.value,
                actor_email="scheduler",
                note=f"{sub.status}: reminder #{count + 1} to {len(recipients | cc)} recipient(s)",
            )
        )
        db.commit()
        log.info("reminder sent for %s (%s)", sub.reference_no, sub.status)
    notify.send_pending(db)


def run_review_cycles(db: Session) -> None:
    """Six-month content review: notice before due date, then weekly overdue reminders."""
    s = get_settings()
    now = utcnow()
    cycles = (
        db.execute(select(ReviewCycle).where(ReviewCycle.closed_reason.is_(None)).order_by(ReviewCycle.due_at))
        .scalars()
        .all()
    )
    for cycle in cycles:
        sub = cycle.submission
        if sub.status != Status.PUBLISHED.value or sub.archived_at is not None:
            continue
        notice_at = cycle.due_at - timedelta(days=s.review_notice_days_before)
        if cycle.notified_at is None and now >= notice_at:
            notify.queue(db, "review_due", sub.owner_emails, sub, due=cycle.due_at)
            cycle.notified_at = now
            cycle.last_reminder_at = now
            db.commit()
            continue
        if now > cycle.due_at and (
            cycle.last_reminder_at is None or (now - cycle.last_reminder_at) >= timedelta(days=7)
        ):
            overdue_days = (now - cycle.due_at).days
            recipients = set(sub.owner_emails)
            if overdue_days >= 30:
                recipients |= notify.admins(db)
            notify.queue(db, "review_overdue", recipients, sub, due=cycle.due_at, overdue_days=overdue_days)
            cycle.last_reminder_at = now
            db.commit()
    notify.send_pending(db)


def housekeeping(db: Session) -> None:
    now = utcnow()
    db.execute(delete(MagicLinkToken).where(MagicLinkToken.expires_at < now - timedelta(hours=24)))
    db.execute(delete(UserSession).where(UserSession.absolute_expires_at < now - timedelta(days=30)))
    db.execute(delete(RateLimitCounter).where(RateLimitCounter.window_start < now - timedelta(days=2)))
    db.commit()


# --------------------------------------------------------------------------- lifecycle
_scheduler = None


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None or not get_settings().scheduler_enabled:
        return
    from apscheduler.schedulers.background import BackgroundScheduler

    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(_locked(deliver_notifications), "interval", seconds=60, id="deliver", max_instances=1, coalesce=True)
    sched.add_job(_locked(send_action_reminders), "cron", minute=15, id="reminders", max_instances=1, coalesce=True)
    sched.add_job(_locked(run_review_cycles), "cron", minute=30, id="review_cycles", max_instances=1, coalesce=True)
    sched.add_job(_locked(housekeeping), "cron", hour=3, minute=5, id="housekeeping", max_instances=1, coalesce=True)
    sched.start()
    _scheduler = sched
    log.info("scheduler started")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
