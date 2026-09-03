"""Single source of truth for authorisation. Route handlers call these; they never re-implement checks.

Mirrors the permission matrix in docs/02-workflow-and-rbac.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.enums import Role, Status
from app.models import Submission, User


def _has(user: User, role: Role, sub: Submission | None = None) -> bool:
    return user.has_role(role, sub.business_group_id if sub is not None else None)


def is_admin(user: User) -> bool:
    return user.is_admin


def is_contact(user: User, sub: Submission) -> bool:
    return sub.is_contact(user.email)


def is_staff(user: User) -> bool:
    """Anyone with a workflow role (may see the list of all submissions in scope)."""
    return any(user.has_role(r) for r in (Role.REVIEWER, Role.APPROVER, Role.PUBLISHER, Role.ADMIN))


# --------------------------------------------------------------------------- viewing
def can_view(user: User, sub: Submission) -> bool:
    if is_admin(user) or is_contact(user, sub) or _has(user, Role.PUBLISHER):
        return True
    return _has(user, Role.REVIEWER, sub) or _has(user, Role.APPROVER, sub)


def can_view_all_list(user: User) -> bool:
    return is_staff(user)


# --------------------------------------------------------------------------- editing
def can_edit(user: User, sub: Submission) -> bool:
    st = sub.status_enum
    if is_admin(user):
        return not st.is_terminal
    return is_contact(user, sub) and st.is_editable


def can_manage_attachments(user: User, sub: Submission) -> bool:
    return can_edit(user, sub)


def can_manage_contacts(user: User, sub: Submission) -> bool:
    return can_edit(user, sub)


def can_comment(user: User, sub: Submission) -> bool:
    return can_view(user, sub) and not sub.status_enum.is_terminal


def can_withdraw(user: User, sub: Submission) -> bool:
    st = sub.status_enum
    if st.is_terminal or st == Status.PUBLISHED:
        return False
    return is_admin(user) or is_contact(user, sub)


# --------------------------------------------------------------------------- review
def can_claim(user: User, sub: Submission) -> bool:
    return sub.status_enum == Status.SUBMITTED and (is_admin(user) or _has(user, Role.REVIEWER, sub))


def can_request_updates(user: User, sub: Submission) -> bool:
    st = sub.status_enum
    if st == Status.UNDER_REVIEW:
        return is_admin(user) or _has(user, Role.REVIEWER, sub)
    if st == Status.AWAITING_APPROVAL:
        return is_admin(user) or _has(user, Role.APPROVER, sub)
    return False


def can_resubmit(user: User, sub: Submission) -> bool:
    return sub.status_enum == Status.UPDATES_REQUIRED and (is_admin(user) or is_contact(user, sub))


def can_complete_review(user: User, sub: Submission) -> bool:
    if sub.status_enum != Status.UNDER_REVIEW:
        return False
    if is_contact(user, sub) and not is_admin(user):
        return False  # separation of duties
    return is_admin(user) or _has(user, Role.REVIEWER, sub)


# --------------------------------------------------------------------------- approval
def can_decide_approval(user: User, sub: Submission) -> bool:
    if sub.status_enum != Status.AWAITING_APPROVAL:
        return False
    if is_contact(user, sub):
        return False  # nobody approves their own offering, admins included
    return is_admin(user) or _has(user, Role.APPROVER, sub)


# --------------------------------------------------------------------------- publishing
def can_confirm_ready(user: User, sub: Submission) -> bool:
    return sub.status_enum == Status.APPROVED and (is_admin(user) or _has(user, Role.PUBLISHER))


def can_publish(user: User, sub: Submission) -> bool:
    return sub.status_enum == Status.READY_TO_PUBLISH and (is_admin(user) or _has(user, Role.PUBLISHER))


def can_export(user: User, sub: Submission) -> bool:
    if is_admin(user) or _has(user, Role.PUBLISHER):
        return True
    return _has(user, Role.REVIEWER, sub) or _has(user, Role.APPROVER, sub)


# --------------------------------------------------------------------------- maintenance
def can_confirm_review(user: User, sub: Submission) -> bool:
    return sub.status_enum == Status.PUBLISHED and (is_admin(user) or is_contact(user, sub))


def can_reopen(user: User, sub: Submission) -> bool:
    st = sub.status_enum
    if st == Status.PUBLISHED:
        return is_admin(user) or is_contact(user, sub)
    if st in (Status.REJECTED, Status.WITHDRAWN):
        return is_admin(user)
    return False


def can_archive(user: User, sub: Submission) -> bool:
    return is_admin(user)


def can_view_audit(user: User, sub: Submission) -> bool:
    return can_view(user, sub)


# --------------------------------------------------------------------------- action list for the UI
@dataclass(frozen=True)
class Action:
    key: str
    label: str
    style: str = "secondary"  # primary | secondary | danger
    needs_note: bool = False
    note_label: str = "Note"


ACTIONS: dict[str, Action] = {
    "claim": Action("claim", "Start review", "primary"),
    "request_updates": Action("request_updates", "Request updates", "secondary", True, "What needs to change?"),
    "resubmit": Action("resubmit", "Resubmit for review", "primary", False),
    "complete_review": Action("complete_review", "Mark review complete", "primary", False),
    "approve": Action("approve", "Approve", "primary", False),
    "reject": Action("reject", "Reject", "danger", True, "Reason for rejection"),
    "confirm_ready": Action("confirm_ready", "Confirm ready to publish", "primary", False),
    "publish": Action("publish", "Record publication", "primary", False),
    "reopen": Action("reopen", "Reopen for updates", "secondary", True, "Why is this being reopened?"),
    "withdraw": Action("withdraw", "Withdraw", "danger", True, "Reason for withdrawing"),
    "confirm_review": Action("confirm_review", "Confirm content is still current", "primary", False),
}


def available_actions(user: User, sub: Submission) -> list[Action]:
    out: list[Action] = []
    if can_claim(user, sub):
        out.append(ACTIONS["claim"])
    if can_resubmit(user, sub):
        out.append(ACTIONS["resubmit"])
    if can_complete_review(user, sub):
        out.append(ACTIONS["complete_review"])
    if can_decide_approval(user, sub):
        out.append(ACTIONS["approve"])
    if can_request_updates(user, sub):
        out.append(ACTIONS["request_updates"])
    if can_decide_approval(user, sub):
        out.append(ACTIONS["reject"])
    if can_confirm_ready(user, sub):
        out.append(ACTIONS["confirm_ready"])
    if can_publish(user, sub):
        out.append(ACTIONS["publish"])
    if can_confirm_review(user, sub):
        out.append(ACTIONS["confirm_review"])
    if can_reopen(user, sub):
        out.append(ACTIONS["reopen"])
    if can_withdraw(user, sub):
        out.append(ACTIONS["withdraw"])
    return out
