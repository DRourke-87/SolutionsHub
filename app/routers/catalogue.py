"""Catalogue of approved and published Solutions / Offerings – the landing page for every signed-in user."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import policy
from app.auth import require_user
from app.db import get_db
from app.enums import READINESS_LABELS, ReadinessLevel, Status
from app.models import (
    BusinessGroup,
    Capability,
    CapabilityArea,
    PageView,
    Submission,
    SubmissionCapability,
    User,
)
from app.services.export import snapshot
from app.templating import render

router = APIRouter(tags=["catalogue"])

_STATUSES = [s.value for s in policy.CATALOGUE_STATUSES]


def _base_query():
    return (
        select(Submission)
        .options(
            selectinload(Submission.business_group),
            selectinload(Submission.contacts),
            selectinload(Submission.capabilities)
            .selectinload(SubmissionCapability.capability)
            .selectinload(Capability.area),
        )
        .where(Submission.status.in_(_STATUSES), Submission.archived_at.is_(None))
    )


def _needs_action_count(db: Session, user: User) -> int:
    if not policy.is_staff(user):
        return 0
    from app.routers.submissions import _needs_me, _scope_filter

    stmt = select(Submission).where(Submission.archived_at.is_(None), Submission.status != Status.DRAFT.value)
    scope = _scope_filter(user)
    if scope is not None:
        stmt = stmt.where(scope)
    return sum(1 for sub in db.execute(stmt).scalars() if _needs_me(user, sub))


@router.get("/")
@router.get("/catalogue")
def catalogue(
    request: Request,
    q: str = "",
    area: str = "",
    business_group_id: str = "",
    readiness: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    # Filters arrive as strings (HTML forms send "" for "no selection"); parse leniently.
    area = int(area) if area.isdigit() else None
    business_group_id = int(business_group_id) if business_group_id.isdigit() else None
    stmt = _base_query()
    if area:
        stmt = stmt.where(
            Submission.id.in_(
                select(SubmissionCapability.submission_id)
                .join(Capability, Capability.id == SubmissionCapability.capability_id)
                .where(Capability.area_id == area)
            )
        )
    if business_group_id:
        stmt = stmt.where(Submission.business_group_id == business_group_id)
    if readiness in {r.value for r in ReadinessLevel}:
        stmt = stmt.where(Submission.readiness_level == readiness)
    else:
        readiness = ""
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Submission.offering_name.ilike(like),
                Submission.customer_challenge.ilike(like),
                Submission.technical_description.ilike(like),
                Submission.key_benefits.ilike(like),
            )
        )
    items = (
        db.execute(
            stmt.order_by(
                func.coalesce(Submission.published_at, Submission.approved_at).desc(), Submission.offering_name
            )
        )
        .scalars()
        .all()
    )

    # Filter reference data with counts across the whole catalogue (not the current filter)
    area_counts = dict(
        db.execute(
            select(Capability.area_id, func.count(func.distinct(SubmissionCapability.submission_id)))
            .join(SubmissionCapability, SubmissionCapability.capability_id == Capability.id)
            .join(Submission, Submission.id == SubmissionCapability.submission_id)
            .where(Submission.status.in_(_STATUSES), Submission.archived_at.is_(None))
            .group_by(Capability.area_id)
        ).all()
    )
    areas = db.execute(select(CapabilityArea).order_by(CapabilityArea.sort_order)).scalars().all()
    groups = db.execute(select(BusinessGroup).order_by(BusinessGroup.sort_order, BusinessGroup.name)).scalars().all()
    total = (
        db.execute(
            select(func.count(Submission.id)).where(Submission.status.in_(_STATUSES), Submission.archived_at.is_(None))
        ).scalar()
        or 0
    )
    ctx = dict(
        items=items,
        areas=areas,
        area_counts=area_counts,
        groups=groups,
        readiness_options=[(r.value, READINESS_LABELS[r]) for r in ReadinessLevel],
        q=q,
        area=area,
        business_group_id=business_group_id,
        readiness=readiness,
        total=total,
        filtered=bool(q.strip() or area or business_group_id or readiness),
    )
    if request.headers.get("hx-request") == "true":
        return render(request, "partials/gallery_grid.html", **ctx)
    ctx["needs_action"] = _needs_action_count(db, user)
    return render(request, "catalogue.html", **ctx)


@router.get("/catalogue/{submission_id}")
def catalogue_detail(
    submission_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    sub = db.execute(
        _base_query()
        .options(
            selectinload(Submission.attachments),
            selectinload(Submission.versions),
            selectinload(Submission.publish_destination),
        )
        .where(Submission.id == submission_id)
    ).scalar_one_or_none()
    if sub is None:
        # Not approved content. People who may see the working record go there; everyone else gets 404.
        live = db.get(Submission, submission_id)
        if live is not None and policy.can_view(user, live):
            return RedirectResponse(f"/submissions/{submission_id}", status_code=303)
        raise HTTPException(404, "This offering is not in the catalogue")
    approved = sub.versions[-1] if sub.versions else None
    snap = approved.snapshot if approved else snapshot(sub)
    db.add(PageView(submission_id=sub.id, viewer_email=user.email, kind="catalogue_view"))
    db.commit()
    owners = [c for c in snap.get("contacts", []) if c.get("role") in ("owner", "co_lead", "solution_architect")]
    related = (
        db.execute(
            _base_query()
            .where(Submission.id != sub.id, Submission.business_group_id == sub.business_group_id)
            .order_by(func.coalesce(Submission.published_at, Submission.approved_at).desc())
            .limit(3)
        )
        .scalars()
        .all()
    )
    return render(
        request,
        "catalogue_detail.html",
        sub=sub,
        snap=snap,
        owners=owners,
        approved_revision=approved.revision if approved else sub.revision,
        newer_in_progress=bool(approved and sub.revision > approved.revision),
        can_view_full=policy.can_view(user, sub),
        related=related,
    )
