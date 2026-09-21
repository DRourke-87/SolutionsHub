from __future__ import annotations

import enum


class StrEnum(enum.StrEnum):
    """String enum whose str() is the raw value (matches what is stored in the database)."""


class Status(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    UPDATES_REQUIRED = "updates_required"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    READY_TO_PUBLISH = "ready_to_publish"
    PUBLISHED = "published"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"

    @property
    def label(self) -> str:
        return STATUS_LABELS[self]

    @property
    def is_terminal(self) -> bool:
        return self in {Status.REJECTED, Status.WITHDRAWN}

    @property
    def is_editable(self) -> bool:
        return self in {Status.DRAFT, Status.SUBMITTED, Status.UPDATES_REQUIRED}


STATUS_LABELS = {
    Status.DRAFT: "Draft",
    Status.SUBMITTED: "Submitted",
    Status.UNDER_REVIEW: "Under Review",
    Status.UPDATES_REQUIRED: "Awaiting Edits",
    Status.AWAITING_APPROVAL: "Awaiting Approval",
    Status.APPROVED: "Approved",
    Status.READY_TO_PUBLISH: "Ready to Publish",
    Status.PUBLISHED: "Published",
    Status.REJECTED: "Not Approved",
    Status.WITHDRAWN: "Withdrawn",
}

# The seven stages from the requirements, in order, for progress display
PIPELINE_STAGES = [
    Status.SUBMITTED,
    Status.UNDER_REVIEW,
    Status.UPDATES_REQUIRED,
    Status.AWAITING_APPROVAL,
    Status.APPROVED,
    Status.READY_TO_PUBLISH,
    Status.PUBLISHED,
]


class Stage(StrEnum):
    """The five stages a submitter sees. Several internal statuses map onto one stage."""

    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    AWAITING_EDITS = "awaiting_edits"
    APPROVED = "approved"
    PUBLISHED = "published"


STAGE_LABELS = {
    Stage.SUBMITTED: "Submitted",
    Stage.UNDER_REVIEW: "Under Review",
    Stage.AWAITING_EDITS: "Awaiting Edits",
    Stage.APPROVED: "Approved",
    Stage.PUBLISHED: "Published",
}

DISPLAY_STAGES = [
    Stage.SUBMITTED,
    Stage.UNDER_REVIEW,
    Stage.AWAITING_EDITS,
    Stage.APPROVED,
    Stage.PUBLISHED,
]

STATUS_STAGE = {
    Status.SUBMITTED: Stage.SUBMITTED,
    Status.UNDER_REVIEW: Stage.UNDER_REVIEW,
    Status.AWAITING_APPROVAL: Stage.UNDER_REVIEW,
    Status.UPDATES_REQUIRED: Stage.AWAITING_EDITS,
    Status.APPROVED: Stage.APPROVED,
    Status.READY_TO_PUBLISH: Stage.APPROVED,
    Status.PUBLISHED: Stage.PUBLISHED,
}


def stage_for(status: Status | str | None) -> Stage | None:
    """The displayed stage for an internal status; None for draft and terminal states."""
    if status is None:
        return None
    try:
        return STATUS_STAGE.get(Status(status))
    except ValueError:
        return None


class Role(StrEnum):
    REVIEWER = "reviewer"
    APPROVER = "approver"
    PUBLISHER = "publisher"
    ADMIN = "admin"


class ContactRole(StrEnum):
    RECORDER = "recorder"
    OWNER_TECHNICAL = "owner_technical"
    OWNER_OPERATIONS = "owner_operations"


CONTACT_ROLE_LABELS = {
    ContactRole.RECORDER: "Recorder",
    ContactRole.OWNER_TECHNICAL: "Owner - Technical",
    ContactRole.OWNER_OPERATIONS: "Owner - Operations",
}

# Roles a submitter can pick for the people they name (the recorder is always the signed-in user)
SELECTABLE_CONTACT_ROLES = [ContactRole.OWNER_TECHNICAL, ContactRole.OWNER_OPERATIONS]

OWNER_CONTACT_ROLES = {ContactRole.OWNER_TECHNICAL, ContactRole.OWNER_OPERATIONS}


class ReadinessLevel(StrEnum):
    CONCEPTUAL = "conceptual"
    PROTOTYPE = "prototype"
    TEST_PHASE = "test_phase"
    SINGLE_DEPLOYMENT = "single_deployment"
    MULTI_CLIENT_DEPLOYMENT = "multi_client_deployment"


READINESS_LABELS = {
    ReadinessLevel.CONCEPTUAL: "Conceptual / White Paper",
    ReadinessLevel.PROTOTYPE: "Prototype",
    ReadinessLevel.TEST_PHASE: "Pilot / Test Phase",
    ReadinessLevel.SINGLE_DEPLOYMENT: "Single Deployment",
    ReadinessLevel.MULTI_CLIENT_DEPLOYMENT: "Multi-Client Deployment",
}


class EventType(StrEnum):
    TRANSITION = "transition"
    COMMENT = "comment"
    ATTACHMENT_ADDED = "attachment_added"
    ATTACHMENT_REMOVED = "attachment_removed"
    CONTACT_CHANGED = "contact_changed"
    FIELDS_EDITED = "fields_edited"
    ROLE_GRANTED = "role_granted"
    ROLE_REVOKED = "role_revoked"
    SIGN_IN = "sign_in"
    REVIEW_CONFIRMED = "review_confirmed"
    EXPORT_DOWNLOADED = "export_downloaded"
    REMINDER_SENT = "reminder_sent"
    SENSITIVE_DATA_ACK = "sensitive_data_ack"


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
