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
    Status.UPDATES_REQUIRED: "Updates Required",
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


class Role(StrEnum):
    REVIEWER = "reviewer"
    APPROVER = "approver"
    PUBLISHER = "publisher"
    ADMIN = "admin"


class ContactRole(StrEnum):
    RECORDER = "recorder"
    OWNER = "owner"
    CO_LEAD = "co_lead"
    SOLUTION_ARCHITECT = "solution_architect"


CONTACT_ROLE_LABELS = {
    ContactRole.RECORDER: "Recorder",
    ContactRole.OWNER: "Offering Owner",
    ContactRole.CO_LEAD: "Co-lead / Backup",
    ContactRole.SOLUTION_ARCHITECT: "Solution Architect",
}


class ReadinessLevel(StrEnum):
    CONCEPTUAL = "conceptual"
    PROTOTYPE = "prototype"
    TEST_PHASE = "test_phase"
    SINGLE_DEPLOYMENT = "single_deployment"
    MULTI_CLIENT_DEPLOYMENT = "multi_client_deployment"


READINESS_LABELS = {
    ReadinessLevel.CONCEPTUAL: "Conceptual",
    ReadinessLevel.PROTOTYPE: "Prototype",
    ReadinessLevel.TEST_PHASE: "Test Phase",
    ReadinessLevel.SINGLE_DEPLOYMENT: "Single Deployment",
    ReadinessLevel.MULTI_CLIENT_DEPLOYMENT: "Multi-Client Deployment",
}


class DeploymentStatus(StrEnum):
    DEPLOYED = "deployed"
    PROPOSED = "proposed"
    BOTH = "both"
    NEITHER = "neither"


DEPLOYMENT_LABELS = {
    DeploymentStatus.DEPLOYED: "Currently deployed",
    DeploymentStatus.PROPOSED: "Included on a proposal",
    DeploymentStatus.BOTH: "Deployed and proposed",
    DeploymentStatus.NEITHER: "Neither yet",
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


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"
