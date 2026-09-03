from __future__ import annotations

from app import policy
from app.enums import ContactRole, Role, Status
from app.models import Submission, SubmissionContact, User, UserRole


def _user(email: str, *roles: Role, bg: int | None = None) -> User:
    u = User(email=email, domain="amentum.com")
    u.roles = [UserRole(email=email, role=r.value, business_group_id=bg, granted_by_email="t") for r in roles]
    return u


def _sub(status: Status, owner: str = "owner@amentum.com", bg: int = 1) -> Submission:
    s = Submission(offering_name="X", created_by_email=owner, status=status.value, business_group_id=bg)
    s.contacts = [SubmissionContact(contact_role=ContactRole.OWNER.value, name="O", email=owner)]
    return s


def test_owner_can_edit_only_in_editable_states():
    owner = _user("owner@amentum.com")
    assert policy.can_edit(owner, _sub(Status.SUBMITTED))
    assert policy.can_edit(owner, _sub(Status.UPDATES_REQUIRED))
    assert not policy.can_edit(owner, _sub(Status.UNDER_REVIEW))
    assert not policy.can_edit(owner, _sub(Status.APPROVED))


def test_stranger_cannot_view():
    stranger = _user("other@amentum.com")
    assert not policy.can_view(stranger, _sub(Status.SUBMITTED))


def test_reviewer_scope_by_business_group():
    reviewer = _user("rev@amentum.com", Role.REVIEWER, bg=2)
    assert not policy.can_view(reviewer, _sub(Status.SUBMITTED, bg=1))
    assert policy.can_view(reviewer, _sub(Status.SUBMITTED, bg=2))
    global_reviewer = _user("rev2@amentum.com", Role.REVIEWER)
    assert policy.can_claim(global_reviewer, _sub(Status.SUBMITTED, bg=1))


def test_separation_of_duties_for_approval():
    approver_owner = _user("owner@amentum.com", Role.APPROVER)
    assert not policy.can_decide_approval(approver_owner, _sub(Status.AWAITING_APPROVAL))
    admin_owner = _user("owner@amentum.com", Role.ADMIN)
    assert not policy.can_decide_approval(admin_owner, _sub(Status.AWAITING_APPROVAL))
    approver = _user("app@amentum.com", Role.APPROVER)
    assert policy.can_decide_approval(approver, _sub(Status.AWAITING_APPROVAL))
    assert not policy.can_decide_approval(approver, _sub(Status.UNDER_REVIEW))


def test_publisher_transitions():
    pub = _user("pub@amentum.com", Role.PUBLISHER)
    assert policy.can_confirm_ready(pub, _sub(Status.APPROVED))
    assert not policy.can_publish(pub, _sub(Status.APPROVED))
    assert policy.can_publish(pub, _sub(Status.READY_TO_PUBLISH))
    assert policy.can_export(pub, _sub(Status.SUBMITTED))
    assert not policy.can_claim(pub, _sub(Status.SUBMITTED))


def test_available_actions_matches_matrix():
    reviewer = _user("rev@amentum.com", Role.REVIEWER)
    keys = [a.key for a in policy.available_actions(reviewer, _sub(Status.SUBMITTED))]
    assert keys == ["claim"]
    keys = [a.key for a in policy.available_actions(reviewer, _sub(Status.UNDER_REVIEW))]
    assert set(keys) == {"complete_review", "request_updates"}
    owner = _user("owner@amentum.com")
    keys = [a.key for a in policy.available_actions(owner, _sub(Status.UPDATES_REQUIRED))]
    assert set(keys) == {"resubmit", "withdraw"}
    keys = [a.key for a in policy.available_actions(owner, _sub(Status.PUBLISHED))]
    assert set(keys) == {"confirm_review", "reopen"}
