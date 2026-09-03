"""Workflow engine: guards, transitions, audit events and notifications for a submission."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import policy
from app.config import get_settings
from app.db import utcnow
from app.enums import EventType, Status
from app.models import (
    ReferenceSequence,
    ReviewCycle,
    Submission,
    SubmissionVersion,
    User,
    WorkflowEvent,
)
from app.services import notifications as notify
from app.services.export import snapshot


class WorkflowError(Exception):
    pass


@dataclass
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None


@dataclass
class TransitionResult:
    submission: Submission
    event: WorkflowEvent
    messages: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers
def mint_reference(db: Session) -> str:
    year = utcnow().year
    seq = (
        db.get(ReferenceSequence, year, with_for_update=True)
        if db.bind.dialect.name != "sqlite"
        else db.get(ReferenceSequence, year)
    )
    if seq is None:
        seq = ReferenceSequence(year=year, last_value=0)
        db.add(seq)
        db.flush()
    seq.last_value += 1
    db.flush()
    return f"SOL-{year}-{seq.last_value:04d}"


def completeness_errors(sub: Submission) -> list[str]:
    """Required-field validation used before Submitted, Resubmit and Awaiting Approval."""
    errors: list[str] = []
    if not (sub.offering_name or "").strip():
        errors.append("Offering name is required.")
    if sub.business_group_id is None:
        errors.append("Business group is required.")
    if sub.cto_aware is None:
        errors.append("Please confirm whether your business group CTO is aware of this request.")
    for label, value in (
        ("Customer challenge", sub.customer_challenge),
        ("Description of technical solution", sub.technical_description),
        ("Key customer benefits", sub.key_benefits),
        ("Current pipeline", sub.current_pipeline),
    ):
        if not (value or "").strip():
            errors.append(f"{label} is required.")
    if not sub.readiness_level:
        errors.append("Level of readiness is required.")
    if not sub.deployment_status:
        errors.append("Currently deployed or proposed is required.")
    if not sub.has_owner:
        errors.append("At least one Offering Owner with an email address is required.")
    n_caps = len(sub.capabilities)
    if n_caps < 1 or n_caps > 3:
        errors.append("Select between 1 and 3 capabilities.")
    for sc in sub.capabilities:
        if sc.capability.is_other and not (sc.other_text or "").strip():
            errors.append("Please specify the 'Other' capability.")
    if not sub.active_attachments and not (sub.resource_links_notes or "").strip():
        errors.append("Provide at least one supporting file or a resource link / note.")
    return errors


def record_event(
    db: Session,
    sub: Submission | None,
    event_type: EventType,
    actor: User | str,
    note: str | None = None,
    from_status: str | None = None,
    to_status: str | None = None,
    meta: RequestMeta | None = None,
) -> WorkflowEvent:
    if isinstance(actor, User):
        actor_email, actor_name = actor.email, actor.display_name
    else:
        actor_email, actor_name = actor, None
    ev = WorkflowEvent(
        submission_id=sub.id if sub is not None else None,
        revision=sub.revision if sub is not None else None,
        event_type=event_type.value,
        from_status=from_status,
        to_status=to_status,
        actor_email=actor_email,
        actor_name=actor_name,
        note=note,
        ip=meta.ip if meta else None,
        user_agent=(meta.user_agent or "")[:400] if meta else None,
    )
    db.add(ev)
    if sub is not None:
        sub.last_action_at = utcnow()
    db.flush()
    return ev


def _set_status(db: Session, sub: Submission, to: Status, actor: User, note: str | None, meta: RequestMeta | None):
    from_status = sub.status
    sub.status = to.value
    return record_event(db, sub, EventType.TRANSITION, actor, note, from_status, to.value, meta)


# --------------------------------------------------------------------------- submit
def submit(db: Session, sub: Submission, actor: User, meta: RequestMeta | None = None) -> TransitionResult:
    """Draft -> Submitted (or first submission of a new record)."""
    if sub.status not in (Status.DRAFT.value, Status.SUBMITTED.value):
        raise WorkflowError("This submission has already been submitted.")
    errors = completeness_errors(sub)
    if errors:
        raise WorkflowError(" ".join(errors))
    if not sub.reference_no:
        sub.reference_no = mint_reference(db)
    sub.submitted_at = utcnow()
    ev = _set_status(db, sub, Status.SUBMITTED, actor, None, meta)
    notify.queue(db, "new_submission", notify.reviewers_for(db, sub), sub)
    notify.queue(db, "submission_received", sub.owner_emails, sub)
    return TransitionResult(sub, ev)


# --------------------------------------------------------------------------- generic action dispatcher
def perform(
    db: Session,
    sub: Submission,
    action: str,
    actor: User,
    note: str | None = None,
    meta: RequestMeta | None = None,
    extra: dict | None = None,
) -> TransitionResult:
    extra = extra or {}
    note = (note or "").strip() or None
    handler = _HANDLERS.get(action)
    if handler is None:
        raise WorkflowError("Unknown action.")
    return handler(db, sub, actor, note, meta, extra)


def _claim(db, sub, actor, note, meta, extra):
    if not policy.can_claim(actor, sub):
        raise WorkflowError("You cannot start a review on this submission.")
    sub.assigned_reviewer_email = actor.email
    ev = _set_status(db, sub, Status.UNDER_REVIEW, actor, note, meta)
    notify.queue(db, "under_review", sub.owner_emails, sub, reviewer=actor)
    return TransitionResult(sub, ev)


def _request_updates(db, sub, actor, note, meta, extra):
    if not policy.can_request_updates(actor, sub):
        raise WorkflowError("You cannot request updates on this submission.")
    if not note:
        raise WorkflowError("Please describe what needs to change.")
    ev = _set_status(db, sub, Status.UPDATES_REQUIRED, actor, note, meta)
    notify.queue(db, "updates_required", sub.owner_emails, sub, requester=actor, note=note)
    return TransitionResult(sub, ev)


def _resubmit(db, sub, actor, note, meta, extra):
    if not policy.can_resubmit(actor, sub):
        raise WorkflowError("You cannot resubmit this submission.")
    errors = completeness_errors(sub)
    if errors:
        raise WorkflowError(" ".join(errors))
    sub.revision += 1
    ev = _set_status(db, sub, Status.UNDER_REVIEW, actor, note, meta)
    recipients = {sub.assigned_reviewer_email} if sub.assigned_reviewer_email else notify.reviewers_for(db, sub)
    notify.queue(db, "resubmitted", recipients, sub, actor=actor)
    return TransitionResult(sub, ev)


def _complete_review(db, sub, actor, note, meta, extra):
    if not policy.can_complete_review(actor, sub):
        raise WorkflowError("You cannot complete the review on this submission.")
    errors = completeness_errors(sub)
    if errors:
        raise WorkflowError("The submission is not complete: " + " ".join(errors))
    blocking = [c for c in sub.comments if c.is_blocking and c.resolved_at is None]
    if blocking:
        raise WorkflowError(f"{len(blocking)} blocking comment(s) must be resolved before approval.")
    sub.review_completed_at = utcnow()
    ev = _set_status(db, sub, Status.AWAITING_APPROVAL, actor, note, meta)
    notify.queue(db, "awaiting_approval", notify.approvers_for(db, sub), sub)
    notify.queue(db, "review_complete", sub.owner_emails, sub)
    return TransitionResult(sub, ev)


def _approve(db, sub, actor, note, meta, extra):
    if not policy.can_decide_approval(actor, sub):
        raise WorkflowError("You cannot approve this submission.")
    sub.approved_at = utcnow()
    ev = _set_status(db, sub, Status.APPROVED, actor, note, meta)
    version = SubmissionVersion(
        submission_id=sub.id, revision=sub.revision, snapshot=snapshot(sub), approval_event_id=ev.id
    )
    db.add(version)
    db.flush()
    sub.approved_version_id = version.id
    notify.queue(db, "approved", sub.owner_emails, sub, approver=actor)
    notify.queue(db, "ready_for_publishing_prep", notify.publishers(db), sub)
    return TransitionResult(sub, ev)


def _reject(db, sub, actor, note, meta, extra):
    if not policy.can_decide_approval(actor, sub):
        raise WorkflowError("You cannot reject this submission.")
    if not note:
        raise WorkflowError("Please give a reason for the rejection.")
    ev = _set_status(db, sub, Status.REJECTED, actor, note, meta)
    recipients = set(sub.owner_emails)
    if sub.assigned_reviewer_email:
        recipients.add(sub.assigned_reviewer_email)
    notify.queue(db, "rejected", recipients, sub, approver=actor, note=note)
    return TransitionResult(sub, ev)


def _confirm_ready(db, sub, actor, note, meta, extra):
    if not policy.can_confirm_ready(actor, sub):
        raise WorkflowError("You cannot confirm publishing readiness.")
    dest_id = extra.get("publish_destination_id")
    if not dest_id:
        raise WorkflowError("Select a publishing destination.")
    sub.publish_destination_id = int(dest_id)
    ev = _set_status(db, sub, Status.READY_TO_PUBLISH, actor, note, meta)
    notify.queue(db, "ready_to_publish", sub.owner_emails, sub)
    return TransitionResult(sub, ev)


def _publish(db, sub, actor, note, meta, extra):
    if not policy.can_publish(actor, sub):
        raise WorkflowError("You cannot record publication.")
    url = (extra.get("published_url") or "").strip()
    if not url:
        raise WorkflowError("Enter the URL where the offering was published.")
    now = utcnow()
    sub.published_url = url
    sub.published_at = now
    sub.next_review_due = now + timedelta(days=30 * get_settings().review_cycle_months)
    ev = _set_status(db, sub, Status.PUBLISHED, actor, note, meta)
    _open_review_cycle(db, sub)
    notify.queue(db, "published", sub.owner_emails, sub)
    return TransitionResult(sub, ev)


def _reopen(db, sub, actor, note, meta, extra):
    if not policy.can_reopen(actor, sub):
        raise WorkflowError("You cannot reopen this submission.")
    if not note:
        raise WorkflowError("Please say why the record is being reopened.")
    was_published = sub.status == Status.PUBLISHED.value
    sub.revision += 1
    target = Status.UNDER_REVIEW if was_published else Status.SUBMITTED
    if was_published:
        _close_open_cycles(db, sub, "updated")
        if not sub.assigned_reviewer_email:
            target = Status.SUBMITTED
    ev = _set_status(db, sub, target, actor, note, meta)
    if target == Status.UNDER_REVIEW and sub.assigned_reviewer_email:
        notify.queue(db, "reopened", {sub.assigned_reviewer_email}, sub, actor=actor, note=note)
    else:
        notify.queue(db, "new_submission", notify.reviewers_for(db, sub), sub)
    return TransitionResult(sub, ev)


def _withdraw(db, sub, actor, note, meta, extra):
    if not policy.can_withdraw(actor, sub):
        raise WorkflowError("You cannot withdraw this submission.")
    if not note:
        raise WorkflowError("Please give a reason for withdrawing.")
    _close_open_cycles(db, sub, "withdrawn")
    ev = _set_status(db, sub, Status.WITHDRAWN, actor, note, meta)
    recipients = set(sub.owner_emails)
    if sub.assigned_reviewer_email:
        recipients.add(sub.assigned_reviewer_email)
    notify.queue(db, "withdrawn", recipients, sub, actor=actor, note=note, exclude={actor.email})
    return TransitionResult(sub, ev)


def _confirm_review(db, sub, actor, note, meta, extra):
    if not policy.can_confirm_review(actor, sub):
        raise WorkflowError("You cannot confirm this content.")
    now = utcnow()
    for cycle in sub.review_cycles:
        if cycle.is_open:
            cycle.confirmed_at = now
            cycle.confirmed_by_email = actor.email
            cycle.closed_reason = "confirmed"
    sub.next_review_due = now + timedelta(days=30 * get_settings().review_cycle_months)
    _open_review_cycle(db, sub)
    ev = record_event(db, sub, EventType.REVIEW_CONFIRMED, actor, note or "Content confirmed current", meta=meta)
    return TransitionResult(sub, ev)


_HANDLERS = {
    "claim": _claim,
    "request_updates": _request_updates,
    "resubmit": _resubmit,
    "complete_review": _complete_review,
    "approve": _approve,
    "reject": _reject,
    "confirm_ready": _confirm_ready,
    "publish": _publish,
    "reopen": _reopen,
    "withdraw": _withdraw,
    "confirm_review": _confirm_review,
}


# --------------------------------------------------------------------------- review cycles
def _open_review_cycle(db: Session, sub: Submission) -> ReviewCycle:
    cycle = ReviewCycle(submission_id=sub.id, due_at=sub.next_review_due or utcnow())
    db.add(cycle)
    db.flush()
    return cycle


def _close_open_cycles(db: Session, sub: Submission, reason: str) -> None:
    for cycle in sub.review_cycles:
        if cycle.is_open:
            cycle.closed_reason = reason


# --------------------------------------------------------------------------- queries
def waiting_on(sub: Submission) -> str:
    st = sub.status_enum
    if st == Status.SUBMITTED:
        return "Reviewer pool"
    if st == Status.UNDER_REVIEW:
        return sub.assigned_reviewer_email or "Reviewer"
    if st == Status.UPDATES_REQUIRED:
        return "Offering owners"
    if st == Status.AWAITING_APPROVAL:
        return "Approvers"
    if st in (Status.APPROVED, Status.READY_TO_PUBLISH):
        return "Publishing team"
    if st == Status.PUBLISHED:
        return "Owners (periodic review)"
    return "—"


def open_submissions(db: Session) -> list[Submission]:
    return list(
        db.execute(
            select(Submission).where(
                Submission.status.notin_([Status.REJECTED.value, Status.WITHDRAWN.value, Status.DRAFT.value]),
                Submission.archived_at.is_(None),
            )
        ).scalars()
    )
