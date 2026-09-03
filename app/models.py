from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow
from app.enums import ContactRole, Role, Status


# --------------------------------------------------------------------------- reference data
class BusinessGroup(Base):
    __tablename__ = "business_groups"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class CapabilityArea(Base):
    __tablename__ = "capability_areas"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    capabilities: Mapped[list[Capability]] = relationship(
        back_populates="area", order_by="Capability.sort_order", cascade="all, delete-orphan"
    )


class Capability(Base):
    __tablename__ = "capabilities"
    id: Mapped[int] = mapped_column(primary_key=True)
    area_id: Mapped[int] = mapped_column(ForeignKey("capability_areas.id"))
    code: Mapped[str] = mapped_column(String(80), unique=True)
    name: Mapped[str] = mapped_column(String(150))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    area: Mapped[CapabilityArea] = relationship(back_populates="capabilities")

    @property
    def is_other(self) -> bool:
        return self.code == "other"


class PublishDestination(Base):
    __tablename__ = "publish_destinations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    base_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AppSetting(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str | None] = mapped_column(String(300))
    updated_by_email: Mapped[str | None] = mapped_column(String(320))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ReferenceSequence(Base):
    """Per-year counter used to mint gap-free reference numbers (SOL-2026-0001)."""

    __tablename__ = "reference_sequences"
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------- submissions
class Submission(Base):
    __tablename__ = "submissions"
    id: Mapped[int] = mapped_column(primary_key=True)
    reference_no: Mapped[str | None] = mapped_column(String(20), unique=True)
    offering_name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(30), default=Status.SUBMITTED.value, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)

    business_group_id: Mapped[int | None] = mapped_column(ForeignKey("business_groups.id"), index=True)
    cto_aware: Mapped[bool | None] = mapped_column(Boolean)
    customer_challenge: Mapped[str] = mapped_column(Text, default="")
    technical_description: Mapped[str] = mapped_column(Text, default="")
    key_benefits: Mapped[str] = mapped_column(Text, default="")
    readiness_level: Mapped[str | None] = mapped_column(String(40))
    readiness_programs: Mapped[str] = mapped_column(Text, default="")
    deployment_status: Mapped[str | None] = mapped_column(String(20))
    deployment_detail: Mapped[str] = mapped_column(Text, default="")
    additional_customers: Mapped[str] = mapped_column(Text, default="")
    current_pipeline: Mapped[str] = mapped_column(Text, default="")
    resource_links_notes: Mapped[str] = mapped_column(Text, default="")

    created_by_email: Mapped[str] = mapped_column(String(320), index=True)
    assigned_reviewer_email: Mapped[str | None] = mapped_column(String(320))
    approved_version_id: Mapped[int | None] = mapped_column(Integer)
    publish_destination_id: Mapped[int | None] = mapped_column(ForeignKey("publish_destinations.id"))
    published_url: Mapped[str | None] = mapped_column(String(1000))

    submitted_at: Mapped[datetime | None]
    review_completed_at: Mapped[datetime | None]
    approved_at: Mapped[datetime | None]
    published_at: Mapped[datetime | None]
    next_review_due: Mapped[datetime | None]
    last_action_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None]

    business_group: Mapped[BusinessGroup | None] = relationship()
    publish_destination: Mapped[PublishDestination | None] = relationship()
    contacts: Mapped[list[SubmissionContact]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="SubmissionContact.id"
    )
    capabilities: Mapped[list[SubmissionCapability]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="Attachment.id"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="Comment.created_at"
    )
    events: Mapped[list[WorkflowEvent]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="WorkflowEvent.created_at"
    )
    versions: Mapped[list[SubmissionVersion]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="SubmissionVersion.created_at"
    )
    review_cycles: Mapped[list[ReviewCycle]] = relationship(
        back_populates="submission", cascade="all, delete-orphan", order_by="ReviewCycle.due_at"
    )

    # ----- convenience
    @property
    def status_enum(self) -> Status:
        return Status(self.status)

    @property
    def contact_emails(self) -> set[str]:
        emails = {c.email.lower() for c in self.contacts if c.email}
        emails.add(self.created_by_email.lower())
        return emails

    @property
    def owner_emails(self) -> set[str]:
        """Everyone who should be notified as an owner: recorder + owners + co-leads + architects."""
        return self.contact_emails

    @property
    def active_attachments(self) -> list[Attachment]:
        return [a for a in self.attachments if a.deleted_at is None]

    def contacts_by_role(self, role: ContactRole) -> list[SubmissionContact]:
        return [c for c in self.contacts if c.contact_role == role.value]

    @property
    def has_owner(self) -> bool:
        return any(c.contact_role == ContactRole.OWNER.value and c.email for c in self.contacts)

    def is_contact(self, email: str | None) -> bool:
        return bool(email) and email.lower() in self.contact_emails

    @property
    def display_ref(self) -> str:
        return self.reference_no or f"DRAFT-{self.id}"


class SubmissionContact(Base):
    __tablename__ = "submission_contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    contact_role: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(320), index=True)
    phone: Mapped[str | None] = mapped_column(String(50))
    submission: Mapped[Submission] = relationship(back_populates="contacts")

    @property
    def role_label(self) -> str:
        from app.enums import CONTACT_ROLE_LABELS

        return CONTACT_ROLE_LABELS.get(ContactRole(self.contact_role), self.contact_role)


class SubmissionCapability(Base):
    __tablename__ = "submission_capabilities"
    __table_args__ = (UniqueConstraint("submission_id", "capability_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    capability_id: Mapped[int] = mapped_column(ForeignKey("capabilities.id"))
    other_text: Mapped[str | None] = mapped_column(String(200))
    submission: Mapped[Submission] = relationship(back_populates="capabilities")
    capability: Mapped[Capability] = relationship()


class Attachment(Base):
    __tablename__ = "attachments"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    blob_path: Mapped[str] = mapped_column(String(600))
    original_filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(150), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    uploaded_by_email: Mapped[str] = mapped_column(String(320))
    uploaded_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None]
    submission: Mapped[Submission] = relationship(back_populates="attachments")


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    author_email: Mapped[str] = mapped_column(String(320))
    author_name: Mapped[str | None] = mapped_column(String(150))
    body: Mapped[str] = mapped_column(Text)
    is_blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    submission: Mapped[Submission] = relationship(back_populates="comments")


class WorkflowEvent(Base):
    """Append-only audit trail. Transitions, approvals, comments, role changes and sign-ins all land here."""

    __tablename__ = "workflow_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id"), index=True)
    revision: Mapped[int | None] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str | None] = mapped_column(String(30))
    actor_email: Mapped[str] = mapped_column(String(320), index=True)
    actor_name: Mapped[str | None] = mapped_column(String(150))
    note: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    submission: Mapped[Submission | None] = relationship(back_populates="events")


class SubmissionVersion(Base):
    """Immutable JSON snapshot taken at each approval."""

    __tablename__ = "submission_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    approval_event_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_events.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    submission: Mapped[Submission] = relationship(back_populates="versions")


class ReviewCycle(Base):
    """Six-month content review tracking for published offerings."""

    __tablename__ = "review_cycles"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    due_at: Mapped[datetime] = mapped_column(index=True)
    notified_at: Mapped[datetime | None]
    last_reminder_at: Mapped[datetime | None]
    confirmed_at: Mapped[datetime | None]
    confirmed_by_email: Mapped[str | None] = mapped_column(String(320))
    closed_reason: Mapped[str | None] = mapped_column(String(40))  # confirmed | updated | withdrawn
    submission: Mapped[Submission] = relationship(back_populates="review_cycles")

    @property
    def is_open(self) -> bool:
        return self.closed_reason is None


class PageView(Base):
    __tablename__ = "page_views"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("submissions.id"), index=True)
    viewer_email: Mapped[str | None] = mapped_column(String(320))
    kind: Mapped[str] = mapped_column(String(20), default="view")  # view | download | export
    viewed_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


# --------------------------------------------------------------------------- identity & access
class User(Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(150))
    domain: Mapped[str] = mapped_column(String(253), index=True)
    first_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_sign_in_at: Mapped[datetime | None]
    is_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    roles: Mapped[list[UserRole]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def active_roles(self) -> list[UserRole]:
        return [r for r in self.roles if r.revoked_at is None]

    def has_role(self, role: Role, business_group_id: int | None = None) -> bool:
        for r in self.active_roles:
            if r.role != role.value:
                continue
            if r.business_group_id is None or business_group_id is None or r.business_group_id == business_group_id:
                return True
        return False

    @property
    def is_admin(self) -> bool:
        return self.has_role(Role.ADMIN)

    @property
    def role_names(self) -> list[str]:
        return sorted({r.role for r in self.active_roles})

    @property
    def name_or_email(self) -> str:
        return self.display_name or self.email


class UserRole(Base):
    __tablename__ = "user_roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(ForeignKey("users.email"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    business_group_id: Mapped[int | None] = mapped_column(ForeignKey("business_groups.id"))
    granted_by_email: Mapped[str] = mapped_column(String(320))
    granted_at: Mapped[datetime] = mapped_column(default=utcnow)
    revoked_at: Mapped[datetime | None]
    revoked_by_email: Mapped[str | None] = mapped_column(String(320))
    user: Mapped[User] = relationship(back_populates="roles")
    business_group: Mapped[BusinessGroup | None] = relationship()


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    redirect_path: Mapped[str] = mapped_column(String(500), default="/")
    remember_me: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    used_at: Mapped[datetime | None]
    request_ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class UserSession(Base):
    __tablename__ = "sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(index=True)
    absolute_expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(400))


class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"
    __table_args__ = (Index("ix_rate_limit_bucket", "scope", "key", "window_start"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[str] = mapped_column(String(40))
    key: Mapped[str] = mapped_column(String(320))
    window_start: Mapped[datetime]
    count: Mapped[int] = mapped_column(Integer, default=0)


class NotificationLog(Base):
    __tablename__ = "notification_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("submissions.id"), index=True)
    to_email: Mapped[str] = mapped_column(String(320), index=True)
    template: Mapped[str] = mapped_column(String(60))
    subject: Mapped[str] = mapped_column(String(300))
    body_text: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime] = mapped_column(default=utcnow)
    sent_at: Mapped[datetime | None]
    provider_message_id: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
