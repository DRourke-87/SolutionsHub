from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import workflow
from app.auth import require_admin, revoke_all_sessions
from app.config import get_settings
from app.db import get_db, utcnow
from app.enums import EventType, Role, Status
from app.models import (
    BusinessGroup,
    Capability,
    CapabilityArea,
    NotificationLog,
    PublishDestination,
    ReviewCycle,
    Submission,
    User,
    UserRole,
    WorkflowEvent,
)
from app.routers.common import csrf_protect
from app.security import email_domain, is_allowed_email, normalise_email
from app.services import notifications as notify
from app.templating import redirect, render

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("")
def overview(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    counts = dict(
        db.execute(
            select(Submission.status, func.count(Submission.id))
            .where(Submission.archived_at.is_(None))
            .group_by(Submission.status)
        ).all()
    )
    now = utcnow()
    overdue = (
        db.execute(
            select(ReviewCycle)
            .options(selectinload(ReviewCycle.submission))
            .where(ReviewCycle.closed_reason.is_(None), ReviewCycle.due_at < now)
            .order_by(ReviewCycle.due_at)
        )
        .scalars()
        .all()
    )
    failed = db.execute(select(func.count(NotificationLog.id)).where(NotificationLog.status == "failed")).scalar() or 0
    queued = db.execute(select(func.count(NotificationLog.id)).where(NotificationLog.status == "queued")).scalar() or 0
    role_counts = dict(
        db.execute(
            select(UserRole.role, func.count(UserRole.id)).where(UserRole.revoked_at.is_(None)).group_by(UserRole.role)
        ).all()
    )
    stale = (
        db.execute(
            select(Submission)
            .where(
                Submission.archived_at.is_(None),
                Submission.status.notin_(
                    [Status.PUBLISHED.value, Status.REJECTED.value, Status.WITHDRAWN.value, Status.DRAFT.value]
                ),
            )
            .order_by(Submission.last_action_at)
            .limit(10)
        )
        .scalars()
        .all()
    )
    return render(
        request,
        "admin/overview.html",
        counts=counts,
        overdue=overdue,
        failed=failed,
        queued=queued,
        role_counts=role_counts,
        stale=stale,
        now=now,
        waiting_on=workflow.waiting_on,
    )


# --------------------------------------------------------------------------- users & roles
@router.get("/users")
def users(request: Request, q: str = "", db: Session = Depends(get_db), user: User = Depends(require_admin)):
    stmt = select(User).options(selectinload(User.roles).selectinload(UserRole.business_group)).order_by(User.email)
    if q:
        stmt = stmt.where(User.email.ilike(f"%{q.strip()}%"))
    rows = db.execute(stmt.limit(300)).scalars().all()
    groups = db.execute(select(BusinessGroup).order_by(BusinessGroup.sort_order, BusinessGroup.name)).scalars().all()
    return render(request, "admin/users.html", rows=rows, groups=groups, q=q, roles=[r for r in Role])


@router.post("/users/grant", dependencies=[Depends(csrf_protect)])
def grant(
    request: Request,
    email: str = Form(...),
    role: str = Form(...),
    business_group_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    email = normalise_email(email)
    if not is_allowed_email(email):
        return redirect("/admin/users", "That email address is not on an allowed domain.", "error")
    if role not in {r.value for r in Role}:
        raise HTTPException(400, "Unknown role")
    bg_id = int(business_group_id) if business_group_id.isdigit() else None
    if role in (Role.PUBLISHER.value, Role.ADMIN.value):
        bg_id = None  # these roles are always global
    target = db.get(User, email)
    if target is None:
        target = User(email=email, domain=email_domain(email))
        db.add(target)
        db.flush()
    dup = db.execute(
        select(UserRole).where(
            UserRole.email == email,
            UserRole.role == role,
            UserRole.revoked_at.is_(None),
            UserRole.business_group_id.is_(bg_id) if bg_id is None else UserRole.business_group_id == bg_id,
        )
    ).scalar_one_or_none()
    if dup:
        return redirect("/admin/users", f"{email} already has the {role} role.", "info")
    db.add(UserRole(email=email, role=role, business_group_id=bg_id, granted_by_email=user.email))
    db.add(
        WorkflowEvent(
            event_type=EventType.ROLE_GRANTED.value,
            actor_email=user.email,
            actor_name=user.display_name,
            note=f"{role} -> {email}" + (f" (group {bg_id})" if bg_id else ""),
        )
    )
    notify.queue(db, "role_changed", [email], None, role=role, granted=True, admin=user)
    db.commit()
    notify.send_pending(db)
    return redirect("/admin/users", f"Granted {role} to {email}.")


@router.post("/users/{email}/revoke/{role_id}", dependencies=[Depends(csrf_protect)])
def revoke(email: str, role_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    row = db.get(UserRole, role_id)
    if row is None or row.email != normalise_email(email) or row.revoked_at is not None:
        raise HTTPException(404)
    if row.role == Role.ADMIN.value and row.email == user.email:
        return redirect("/admin/users", "You cannot revoke your own admin role.", "error")
    row.revoked_at = utcnow()
    row.revoked_by_email = user.email
    db.add(
        WorkflowEvent(
            event_type=EventType.ROLE_REVOKED.value,
            actor_email=user.email,
            actor_name=user.display_name,
            note=f"{row.role} revoked from {row.email}",
        )
    )
    notify.queue(db, "role_changed", [row.email], None, role=row.role, granted=False, admin=user)
    db.commit()
    notify.send_pending(db)
    return redirect("/admin/users", f"Revoked {row.role} from {row.email}.")


@router.post("/users/{email}/toggle-disabled", dependencies=[Depends(csrf_protect)])
def toggle_disabled(email: str, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    target = db.get(User, normalise_email(email))
    if target is None:
        raise HTTPException(404)
    if target.email == user.email:
        return redirect("/admin/users", "You cannot disable your own account.", "error")
    target.is_disabled = not target.is_disabled
    if target.is_disabled:
        revoke_all_sessions(db, target.email)
    db.add(
        WorkflowEvent(
            event_type=EventType.ROLE_REVOKED.value if target.is_disabled else EventType.ROLE_GRANTED.value,
            actor_email=user.email,
            note=f"user {'disabled' if target.is_disabled else 'enabled'}: {target.email}",
        )
    )
    db.commit()
    return redirect("/admin/users", f"{target.email} {'disabled' if target.is_disabled else 'enabled'}.")


# --------------------------------------------------------------------------- reference data
@router.get("/reference")
def reference(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    groups = db.execute(select(BusinessGroup).order_by(BusinessGroup.sort_order, BusinessGroup.name)).scalars().all()
    areas = (
        db.execute(
            select(CapabilityArea)
            .options(selectinload(CapabilityArea.capabilities))
            .order_by(CapabilityArea.sort_order)
        )
        .scalars()
        .all()
    )
    dests = db.execute(select(PublishDestination).order_by(PublishDestination.name)).scalars().all()
    return render(request, "admin/reference.html", groups=groups, areas=areas, dests=dests)


@router.post("/reference/business-groups", dependencies=[Depends(csrf_protect)])
def add_group(name: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    name = name.strip()[:150]
    if name and not db.execute(select(BusinessGroup).where(BusinessGroup.name == name)).first():
        max_order = db.execute(select(func.max(BusinessGroup.sort_order))).scalar() or 0
        db.add(BusinessGroup(name=name, sort_order=max_order + 1))
        db.commit()
    return redirect("/admin/reference", f"Business group '{name}' added.")


@router.post("/reference/business-groups/{group_id}/toggle", dependencies=[Depends(csrf_protect)])
def toggle_group(group_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    g = db.get(BusinessGroup, group_id)
    if g is None:
        raise HTTPException(404)
    g.is_active = not g.is_active
    db.commit()
    return redirect("/admin/reference")


@router.post("/reference/capabilities/{cap_id}/toggle", dependencies=[Depends(csrf_protect)])
def toggle_capability(cap_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    c = db.get(Capability, cap_id)
    if c is None:
        raise HTTPException(404)
    c.is_active = not c.is_active
    db.commit()
    return redirect("/admin/reference")


@router.post("/reference/capabilities", dependencies=[Depends(csrf_protect)])
def add_capability(
    area_id: int = Form(...), name: str = Form(...), db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    area = db.get(CapabilityArea, area_id)
    name = name.strip()[:150]
    if area and name:
        code = "custom_" + "".join(ch if ch.isalnum() else "_" for ch in name.lower())[:60]
        if not db.execute(select(Capability).where(Capability.code == code)).first():
            db.add(Capability(area_id=area.id, code=code, name=name, sort_order=len(area.capabilities) + 1))
            db.commit()
    return redirect("/admin/reference", "Capability added.")


@router.post("/reference/destinations", dependencies=[Depends(csrf_protect)])
def add_destination(
    name: str = Form(...), base_url: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    name = name.strip()[:150]
    if name and not db.execute(select(PublishDestination).where(PublishDestination.name == name)).first():
        db.add(PublishDestination(name=name, base_url=base_url.strip() or None))
        db.commit()
    return redirect("/admin/reference", "Destination added.")


@router.post("/reference/destinations/{dest_id}/toggle", dependencies=[Depends(csrf_protect)])
def toggle_destination(dest_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    d = db.get(PublishDestination, dest_id)
    if d is None:
        raise HTTPException(404)
    d.is_active = not d.is_active
    db.commit()
    return redirect("/admin/reference")


# --------------------------------------------------------------------------- notifications & audit
@router.get("/notifications")
def notifications(
    request: Request, status: str = "", db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    stmt = select(NotificationLog).order_by(NotificationLog.queued_at.desc()).limit(200)
    if status:
        stmt = stmt.where(NotificationLog.status == status)
    rows = db.execute(stmt).scalars().all()
    return render(request, "admin/notifications.html", rows=rows, status=status)


@router.post("/notifications/{note_id}/retry", dependencies=[Depends(csrf_protect)])
def retry_notification(note_id: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    row = db.get(NotificationLog, note_id)
    if row is None:
        raise HTTPException(404)
    row.status = "queued"
    row.attempts = 0
    row.error = None
    db.commit()
    notify.send_pending(db)
    return redirect("/admin/notifications", "Notification re-queued.")


@router.get("/audit")
def audit(request: Request, q: str = "", db: Session = Depends(get_db), user: User = Depends(require_admin)):
    stmt = (
        select(WorkflowEvent)
        .options(selectinload(WorkflowEvent.submission))
        .order_by(WorkflowEvent.created_at.desc())
        .limit(300)
    )
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where((WorkflowEvent.actor_email.ilike(like)) | (WorkflowEvent.note.ilike(like)))
    rows = db.execute(stmt).scalars().all()
    return render(request, "admin/audit.html", rows=rows, q=q)


@router.get("/settings")
def settings_view(request: Request, user: User = Depends(require_admin)):
    s = get_settings()
    shown = {
        "APP_ENV": s.app_env,
        "BASE_URL": s.base_url,
        "ALLOWED_EMAIL_DOMAINS": s.allowed_email_domains,
        "BOOTSTRAP_ADMIN_EMAIL": s.bootstrap_admin_email or "(not set)",
        "EMAIL_BACKEND": s.email_backend,
        "ACS_SENDER": s.acs_sender,
        "STORAGE_BACKEND": s.storage_backend,
        "MAX_ATTACHMENTS_PER_SUBMISSION": s.max_attachments_per_submission,
        "MAX_ATTACHMENT_MB": s.max_attachment_mb,
        "ALLOWED_ATTACHMENT_EXTENSIONS": s.allowed_attachment_extensions,
        "MAGIC_LINK_TTL_MINUTES": s.magic_link_ttl_minutes,
        "SESSION_SLIDING_HOURS": s.session_sliding_hours,
        "REMINDER_OWNER_DAYS": s.reminder_owner_days,
        "REMINDER_REVIEWER_POOL_DAYS": s.reminder_reviewer_pool_days,
        "REMINDER_ASSIGNED_REVIEWER_DAYS": s.reminder_assigned_reviewer_days,
        "REMINDER_APPROVER_DAYS": s.reminder_approver_days,
        "REMINDER_PUBLISHER_DAYS": s.reminder_publisher_days,
        "REVIEW_CYCLE_MONTHS": s.review_cycle_months,
        "REVIEW_NOTICE_DAYS_BEFORE": s.review_notice_days_before,
        "SCHEDULER_ENABLED": s.scheduler_enabled,
        "APPLICATIONINSIGHTS": "configured" if s.applicationinsights_connection_string else "not configured",
    }
    return render(request, "admin/settings.html", shown=shown)
