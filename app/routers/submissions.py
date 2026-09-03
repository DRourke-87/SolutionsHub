from __future__ import annotations

import hashlib
import io
import os
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload
from starlette.datastructures import UploadFile

from app import policy, workflow
from app.auth import require_user
from app.config import get_settings
from app.db import get_db, get_sessionmaker, utcnow
from app.enums import ContactRole, DeploymentStatus, EventType, ReadinessLevel, Role, Status
from app.models import (
    Attachment,
    BusinessGroup,
    Capability,
    CapabilityArea,
    Comment,
    PageView,
    PublishDestination,
    Submission,
    SubmissionCapability,
    SubmissionContact,
    User,
)
from app.routers.common import csrf_protect
from app.security import client_ip
from app.services import notifications as notify
from app.services.export import build_package
from app.services.storage import blob_path_for, get_storage, sanitise_filename
from app.templating import redirect, render

router = APIRouter(tags=["submissions"])


# --------------------------------------------------------------------------- helpers
def _deliver_in_background(tasks: BackgroundTasks) -> None:
    def _run():
        with get_sessionmaker()() as db:
            notify.send_pending(db)

    tasks.add_task(_run)


def _meta(request: Request) -> workflow.RequestMeta:
    return workflow.RequestMeta(ip=client_ip(request), user_agent=request.headers.get("user-agent"))


def _load(db: Session, submission_id: int) -> Submission:
    sub = db.execute(
        select(Submission)
        .options(
            selectinload(Submission.contacts),
            selectinload(Submission.capabilities)
            .selectinload(SubmissionCapability.capability)
            .selectinload(Capability.area),
            selectinload(Submission.attachments),
            selectinload(Submission.comments),
            selectinload(Submission.events),
            selectinload(Submission.review_cycles),
            selectinload(Submission.business_group),
            selectinload(Submission.publish_destination),
        )
        .where(Submission.id == submission_id)
    ).scalar_one_or_none()
    if sub is None:
        raise HTTPException(404, "Submission not found")
    return sub


def _require_view(user: User, sub: Submission) -> None:
    if not policy.can_view(user, sub):
        raise HTTPException(403, "You do not have access to this submission")


def _reference_data(db: Session) -> dict:
    areas = (
        db.execute(
            select(CapabilityArea)
            .options(selectinload(CapabilityArea.capabilities))
            .order_by(CapabilityArea.sort_order)
        )
        .scalars()
        .all()
    )
    groups = (
        db.execute(
            select(BusinessGroup)
            .where(BusinessGroup.is_active.is_(True))
            .order_by(BusinessGroup.sort_order, BusinessGroup.name)
        )
        .scalars()
        .all()
    )
    return {"areas": areas, "business_groups": groups}


def _scope_filter(user: User):
    """SQL filter for the list page: staff see everything in scope, others only their own records."""
    if user.is_admin or user.has_role(Role.PUBLISHER):
        return None
    clauses = []
    for role in (Role.REVIEWER, Role.APPROVER):
        for r in user.active_roles:
            if r.role == role.value:
                if r.business_group_id is None:
                    return None
                clauses.append(Submission.business_group_id == r.business_group_id)
    own = or_(
        Submission.created_by_email == user.email,
        Submission.id.in_(select(SubmissionContact.submission_id).where(SubmissionContact.email == user.email)),
    )
    clauses.append(own)
    return or_(*clauses)


# --------------------------------------------------------------------------- form parsing
def _apply_form(db: Session, sub: Submission, form, user: User) -> list[str]:
    """Copy form fields onto the submission. Returns validation errors (format only; completeness is separate)."""
    errors: list[str] = []

    def g(name: str) -> str:
        return (form.get(name) or "").strip()

    sub.offering_name = g("offering_name")[:150]
    bg = g("business_group_id")
    sub.business_group_id = int(bg) if bg.isdigit() else None
    cto = g("cto_aware")
    sub.cto_aware = True if cto == "yes" else False if cto == "no" else None
    sub.customer_challenge = g("customer_challenge")
    sub.technical_description = g("technical_description")
    sub.key_benefits = g("key_benefits")
    sub.readiness_level = g("readiness_level") if g("readiness_level") in {r.value for r in ReadinessLevel} else None
    sub.readiness_programs = g("readiness_programs")
    sub.deployment_status = (
        g("deployment_status") if g("deployment_status") in {d.value for d in DeploymentStatus} else None
    )
    sub.deployment_detail = g("deployment_detail")
    sub.additional_customers = g("additional_customers")
    sub.current_pipeline = g("current_pipeline")
    sub.resource_links_notes = g("resource_links_notes")

    # capabilities (1..3)
    cap_ids = []
    for raw in form.getlist("capabilities"):
        if str(raw).isdigit():
            cap_ids.append(int(raw))
    cap_ids = list(dict.fromkeys(cap_ids))
    if len(cap_ids) > 3:
        errors.append("Select at most 3 capabilities.")
        cap_ids = cap_ids[:3]
    existing = {sc.capability_id: sc for sc in sub.capabilities}
    for cid in list(existing):
        if cid not in cap_ids:
            db.delete(existing[cid])
            sub.capabilities.remove(existing[cid])
    caps = {c.id: c for c in db.execute(select(Capability).where(Capability.id.in_(cap_ids or [0]))).scalars()}
    other_text = g("other_text")[:200] or None
    for cid in cap_ids:
        cap = caps.get(cid)
        if cap is None:
            continue
        sc = existing.get(cid) or SubmissionCapability(capability_id=cid)
        sc.capability = cap
        sc.other_text = other_text if cap.is_other else None
        if cid not in existing:
            sub.capabilities.append(sc)

    # contacts: recorder is the signed-in user; owners/co-leads/architects from repeating rows
    names = form.getlist("contact_name")
    emails = form.getlist("contact_email")
    roles = form.getlist("contact_role")
    phones = form.getlist("contact_phone")
    keep = [c for c in sub.contacts if c.contact_role == ContactRole.RECORDER.value]
    if not keep:
        keep.append(
            SubmissionContact(
                contact_role=ContactRole.RECORDER.value,
                name=g("recorder_name") or user.name_or_email,
                email=user.email,
            )
        )
    else:
        keep[0].name = g("recorder_name") or keep[0].name
    for i in range(max(len(names), len(emails))):
        name = (names[i] if i < len(names) else "").strip()
        email = (emails[i] if i < len(emails) else "").strip().lower()
        role = (roles[i] if i < len(roles) else ContactRole.OWNER.value).strip()
        phone = (phones[i] if i < len(phones) else "").strip() or None
        if not name and not email:
            continue
        if role not in {r.value for r in ContactRole} or role == ContactRole.RECORDER.value:
            role = ContactRole.OWNER.value
        if "@" not in email:
            errors.append(f"Contact '{name or email}' needs a valid email address.")
            continue
        keep.append(SubmissionContact(contact_role=role, name=name or email, email=email, phone=phone))
    for c in list(sub.contacts):
        if c not in keep:
            db.delete(c)
    sub.contacts = keep
    if not user.display_name and g("recorder_name"):
        user.display_name = g("recorder_name")[:150]
    return errors


async def _store_uploads(db: Session, sub: Submission, files: list[UploadFile], user: User) -> list[str]:
    errors: list[str] = []
    s = get_settings()
    storage = get_storage()
    current = len(sub.active_attachments)
    for f in files:
        if not f.filename:
            continue
        if current >= s.max_attachments_per_submission:
            errors.append(
                f"Maximum of {s.max_attachments_per_submission} attachments reached; '{f.filename}' was skipped."
            )
            break
        ext = os.path.splitext(f.filename)[1].lower().lstrip(".")
        if ext not in s.allowed_extensions:
            errors.append(f"File type '.{ext}' is not allowed ('{f.filename}').")
            continue
        data = await f.read()
        if len(data) > s.max_attachment_mb * 1024 * 1024:
            errors.append(f"'{f.filename}' exceeds the {s.max_attachment_mb} MB limit.")
            continue
        att = Attachment(
            submission_id=sub.id,
            blob_path="pending",
            original_filename=sanitise_filename(f.filename),
            content_type=f.content_type or "application/octet-stream",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            uploaded_by_email=user.email,
        )
        db.add(att)
        db.flush()
        att.blob_path = blob_path_for(sub.id, att.id, f.filename)
        storage.save(att.blob_path, io.BytesIO(data), att.content_type)
        sub.attachments.append(att)
        current += 1
        workflow.record_event(db, sub, EventType.ATTACHMENT_ADDED, user, note=att.original_filename)
    return errors


# --------------------------------------------------------------------------- dashboard & list
@router.get("/dashboard")
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    mine = (
        db.execute(
            select(Submission)
            .options(selectinload(Submission.business_group))
            .where(
                or_(
                    Submission.created_by_email == user.email,
                    Submission.id.in_(
                        select(SubmissionContact.submission_id).where(SubmissionContact.email == user.email)
                    ),
                ),
                Submission.archived_at.is_(None),
            )
            .order_by(Submission.updated_at.desc())
            .limit(25)
        )
        .scalars()
        .all()
    )

    queue: list[Submission] = []
    counts: dict[str, int] = {}
    if policy.is_staff(user):
        scope = _scope_filter(user)
        base = select(Submission).where(Submission.archived_at.is_(None), Submission.status != Status.DRAFT.value)
        if scope is not None:
            base = base.where(scope)
        rows = (
            db.execute(base.options(selectinload(Submission.business_group)).order_by(Submission.last_action_at))
            .scalars()
            .all()
        )
        for sub in rows:
            counts[sub.status] = counts.get(sub.status, 0) + 1
        queue = [sub for sub in rows if _needs_me(user, sub)][:25]
    return render(request, "dashboard.html", mine=mine, queue=queue, counts=counts)


def _needs_me(user: User, sub: Submission) -> bool:
    return any(
        (
            policy.can_claim(user, sub),
            policy.can_complete_review(user, sub) and sub.assigned_reviewer_email == user.email,
            policy.can_decide_approval(user, sub),
            policy.can_confirm_ready(user, sub),
            policy.can_publish(user, sub),
        )
    )


@router.get("/submissions")
def list_submissions(
    request: Request,
    status: str | None = None,
    q: str | None = None,
    business_group_id: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    business_group_id = int(business_group_id) if business_group_id.isdigit() else None
    stmt = select(Submission).options(selectinload(Submission.business_group)).where(Submission.archived_at.is_(None))
    scope = _scope_filter(user)
    if scope is not None:
        stmt = stmt.where(scope)
    if status and status in {s.value for s in Status}:
        stmt = stmt.where(Submission.status == status)
    if business_group_id:
        stmt = stmt.where(Submission.business_group_id == business_group_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Submission.offering_name.ilike(like), Submission.reference_no.ilike(like)))
    rows = db.execute(stmt.order_by(Submission.last_action_at.desc()).limit(200)).scalars().all()
    groups = db.execute(select(BusinessGroup).order_by(BusinessGroup.sort_order, BusinessGroup.name)).scalars().all()
    return render(
        request,
        "submissions_list.html",
        rows=rows,
        status=status or "",
        q=q or "",
        groups=groups,
        business_group_id=business_group_id,
        can_see_all=policy.can_view_all_list(user),
    )


# --------------------------------------------------------------------------- create / edit
@router.get("/submissions/new")
def new_form(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    sub = Submission(offering_name="", created_by_email=user.email, status=Status.DRAFT.value)
    return render(request, "submission_form.html", sub=sub, errors=[], is_new=True, **_reference_data(db))


@router.post("/submissions/new", dependencies=[Depends(csrf_protect)])
async def create(
    request: Request, tasks: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    form = await request.form()
    sub = Submission(offering_name="", created_by_email=user.email, status=Status.DRAFT.value)
    db.add(sub)
    errors = _apply_form(db, sub, form, user)
    db.flush()
    files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
    errors += await _store_uploads(db, sub, files, user)
    intent = form.get("intent", "submit")
    if intent == "submit" and not errors:
        try:
            workflow.submit(db, sub, user, _meta(request))
        except workflow.WorkflowError as exc:
            errors.append(str(exc))
    if errors and intent == "submit":
        db.rollback()
        # Re-render with the user's input (unsaved) so nothing is lost
        draft = Submission(offering_name="", created_by_email=user.email, status=Status.DRAFT.value)
        _apply_form(db, draft, form, user)
        db.expunge_all()
        return render(
            request,
            "submission_form.html",
            status_code=400,
            sub=draft,
            errors=errors,
            is_new=True,
            selected_caps={int(c) for c in form.getlist("capabilities") if str(c).isdigit()},
            **_reference_data(db),
        )
    workflow.record_event(db, sub, EventType.FIELDS_EDITED, user, note="created")
    db.commit()
    _deliver_in_background(tasks)
    if sub.status == Status.DRAFT.value:
        msg = "Draft saved. Submit it when you are ready." + (" " + " ".join(errors) if errors else "")
        return redirect(f"/submissions/{sub.id}", msg, "info" if not errors else "warning")
    return redirect(f"/submissions/{sub.id}", f"Submitted as {sub.reference_no}. Reviewers have been notified.")


@router.get("/submissions/{submission_id}/edit")
def edit_form(submission_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    sub = _load(db, submission_id)
    if not policy.can_edit(user, sub):
        raise HTTPException(403, "This submission cannot be edited in its current state")
    return render(
        request,
        "submission_form.html",
        sub=sub,
        errors=[],
        is_new=False,
        selected_caps={sc.capability_id for sc in sub.capabilities},
        **_reference_data(db),
    )


@router.post("/submissions/{submission_id}/edit", dependencies=[Depends(csrf_protect)])
async def edit(
    submission_id: int,
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    sub = _load(db, submission_id)
    if not policy.can_edit(user, sub):
        raise HTTPException(403, "This submission cannot be edited in its current state")
    form = await request.form()
    errors = _apply_form(db, sub, form, user)
    files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
    errors += await _store_uploads(db, sub, files, user)
    intent = form.get("intent", "save")
    workflow.record_event(db, sub, EventType.FIELDS_EDITED, user, note="fields updated")
    if intent == "submit" and sub.status == Status.DRAFT.value and not errors:
        try:
            workflow.submit(db, sub, user, _meta(request))
        except workflow.WorkflowError as exc:
            errors.append(str(exc))
    if intent == "resubmit" and sub.status == Status.UPDATES_REQUIRED.value and not errors:
        try:
            workflow.perform(db, sub, "resubmit", user, form.get("note"), _meta(request))
        except workflow.WorkflowError as exc:
            errors.append(str(exc))
    db.commit()
    _deliver_in_background(tasks)
    if errors:
        return redirect(f"/submissions/{sub.id}/edit", " ".join(errors), "warning")
    return redirect(f"/submissions/{sub.id}", "Changes saved.")


# --------------------------------------------------------------------------- detail
@router.get("/submissions/{submission_id}")
def detail(submission_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    sub = _load(db, submission_id)
    _require_view(user, sub)
    db.add(PageView(submission_id=sub.id, viewer_email=user.email, kind="view"))
    db.commit()
    destinations = db.execute(select(PublishDestination).where(PublishDestination.is_active.is_(True))).scalars().all()
    completeness = workflow.completeness_errors(sub)
    open_cycle = next((c for c in sub.review_cycles if c.is_open), None)
    return render(
        request,
        "submission_detail.html",
        sub=sub,
        actions=policy.available_actions(user, sub),
        destinations=destinations,
        completeness=completeness,
        open_cycle=open_cycle,
        view_count=db.execute(select(func.count(PageView.id)).where(PageView.submission_id == sub.id)).scalar() or 0,
    )


@router.post("/submissions/{submission_id}/action", dependencies=[Depends(csrf_protect)])
async def action(
    submission_id: int,
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    sub = _load(db, submission_id)
    _require_view(user, sub)
    form = await request.form()
    action_key = form.get("action", "")
    try:
        workflow.perform(
            db,
            sub,
            action_key,
            user,
            form.get("note"),
            _meta(request),
            extra={
                "publish_destination_id": form.get("publish_destination_id"),
                "published_url": form.get("published_url"),
            },
        )
    except workflow.WorkflowError as exc:
        db.rollback()
        return redirect(f"/submissions/{sub.id}", str(exc), "error")
    db.commit()
    _deliver_in_background(tasks)
    label = policy.ACTIONS.get(action_key)
    return redirect(
        f"/submissions/{sub.id}",
        f"{label.label if label else 'Action'} recorded. Status is now {sub.status_enum.label}.",
    )


# --------------------------------------------------------------------------- comments
@router.post("/submissions/{submission_id}/comments", dependencies=[Depends(csrf_protect)])
async def add_comment(
    submission_id: int,
    request: Request,
    tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    sub = _load(db, submission_id)
    if not policy.can_comment(user, sub):
        raise HTTPException(403, "You cannot comment on this submission")
    form = await request.form()
    body = (form.get("body") or "").strip()
    if not body:
        return redirect(f"/submissions/{sub.id}", "Comment cannot be empty.", "warning")
    is_blocking = bool(form.get("is_blocking")) and (policy.is_staff(user))
    comment = Comment(
        submission_id=sub.id,
        author_email=user.email,
        author_name=user.display_name,
        body=body[:5000],
        is_blocking=is_blocking,
    )
    db.add(comment)
    workflow.record_event(db, sub, EventType.COMMENT, user, note=body[:500])
    recipients = set(sub.owner_emails)
    if sub.assigned_reviewer_email:
        recipients.add(sub.assigned_reviewer_email)
    notify.queue(db, "comment_added", recipients, sub, exclude={user.email}, author=user, body=body)
    db.commit()
    _deliver_in_background(tasks)
    return redirect(f"/submissions/{sub.id}#comments", "Comment added.")


@router.post("/submissions/{submission_id}/comments/{comment_id}/resolve", dependencies=[Depends(csrf_protect)])
def resolve_comment(
    submission_id: int, comment_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    sub = _load(db, submission_id)
    if not (policy.is_staff(user) or policy.is_contact(user, sub)):
        raise HTTPException(403)
    comment = db.get(Comment, comment_id)
    if comment is None or comment.submission_id != sub.id:
        raise HTTPException(404)
    comment.resolved_at = None if comment.resolved_at else utcnow()
    db.commit()
    return redirect(f"/submissions/{sub.id}#comments")


# --------------------------------------------------------------------------- attachments
@router.post("/submissions/{submission_id}/attachments", dependencies=[Depends(csrf_protect)])
async def upload(
    submission_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    sub = _load(db, submission_id)
    if not policy.can_manage_attachments(user, sub):
        raise HTTPException(403, "Attachments cannot be changed in the current state")
    form = await request.form()
    files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
    errors = await _store_uploads(db, sub, files, user)
    db.commit()
    return redirect(
        f"/submissions/{sub.id}#attachments",
        " ".join(errors) if errors else "Attachment(s) added.",
        "warning" if errors else "success",
    )


@router.post("/submissions/{submission_id}/attachments/{attachment_id}/delete", dependencies=[Depends(csrf_protect)])
def delete_attachment(
    submission_id: int, attachment_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    sub = _load(db, submission_id)
    if not policy.can_manage_attachments(user, sub):
        raise HTTPException(403)
    att = db.get(Attachment, attachment_id)
    if att is None or att.submission_id != sub.id or att.deleted_at:
        raise HTTPException(404)
    att.deleted_at = utcnow()
    get_storage().delete(att.blob_path)
    workflow.record_event(db, sub, EventType.ATTACHMENT_REMOVED, user, note=att.original_filename)
    db.commit()
    return redirect(f"/submissions/{sub.id}#attachments", "Attachment removed.")


@router.get("/submissions/{submission_id}/attachments/{attachment_id}/download")
def download(submission_id: int, attachment_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    sub = _load(db, submission_id)
    if not policy.can_download_attachment(user, sub):
        raise HTTPException(403, "You do not have access to this file")
    att = db.get(Attachment, attachment_id)
    if att is None or att.submission_id != sub.id or att.deleted_at:
        raise HTTPException(404)
    data = get_storage().read(att.blob_path)
    db.add(PageView(submission_id=sub.id, viewer_email=user.email, kind="download"))
    db.commit()
    headers = {"Content-Disposition": f'attachment; filename="{att.original_filename}"'}
    return StreamingResponse(
        io.BytesIO(data), media_type=att.content_type or "application/octet-stream", headers=headers
    )


# --------------------------------------------------------------------------- export
@router.get("/submissions/{submission_id}/export.zip")
def export(submission_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    sub = _load(db, submission_id)
    if not policy.can_export(user, sub):
        raise HTTPException(403, "You cannot export this submission")
    data = build_package(sub)
    workflow.record_event(db, sub, EventType.EXPORT_DOWNLOADED, user)
    db.add(PageView(submission_id=sub.id, viewer_email=user.email, kind="export"))
    db.commit()
    name = f"{sub.reference_no or 'draft'}-{sanitise_filename(sub.offering_name)[:40]}.zip"
    return Response(
        content=data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


__all__ = ["router", "timedelta"]
