"""Drives the full lifecycle through the HTTP layer: submit → review → updates → approve → publish → review cycle."""

from __future__ import annotations

import io
import zipfile
from datetime import timedelta

from sqlalchemy import select

from app.db import utcnow
from app.enums import Role, Status
from app.models import BusinessGroup, Capability, Submission
from app.services.scheduler import business_days_between, run_review_cycles, send_action_reminders
from tests.conftest import grant, login

SUBMITTER = "sam.submitter@amentum.com"
REVIEWER = "rita.reviewer@global.amentum.com"
APPROVER = "andy.approver@amentumcms.com"
PUBLISHER = "pat.publisher@amentum.com"


def _form(db, **overrides):
    bg = db.execute(select(BusinessGroup)).scalars().first()
    caps = db.execute(select(Capability).where(Capability.code.in_(["dt_cloud", "dac_cyber_training"]))).scalars().all()
    data = {
        "offering_name": "Cloud Cyber Range",
        "business_group_id": str(bg.id),
        "recorder_name": "Sam Submitter",
        "cto_aware": "yes",
        "customer_challenge": "Customers cannot train cyber teams on realistic cloud infrastructure.",
        "technical_description": "A cloud-hosted, on-demand cyber range built on IaC templates.",
        "key_benefits": "- Deploy in hours\n- Realistic\n- Cost effective",
        "readiness_level": "prototype",
        "readiness_programs": "Program X",
        "deployment_status": "proposed",
        "deployment_detail": "Proposed on Program X recompete",
        "additional_customers": "Allied defence ministries",
        "current_pipeline": "Program X recompete, $40M",
        "resource_links_notes": "https://example.internal/cyber-range",
        "contact_name": ["Olivia Owner", "Chris Colead"],
        "contact_email": ["olivia.owner@amentum.com", "chris.colead@amentum.com"],
        "contact_role": ["owner", "co_lead"],
        "contact_phone": ["", ""],
        "capabilities": [str(c.id) for c in caps],
        "intent": "submit",
    }
    data.update(overrides)
    return data


def _post_action(client, token, sub_id, action, **fields):
    data = {"csrf_token": token, "action": action, "note": fields.pop("note", "")}
    data.update(fields)
    return client.post(f"/submissions/{sub_id}/action", data=data)


def test_full_lifecycle(client, outbox, db):
    grant(db, REVIEWER, Role.REVIEWER)
    grant(db, APPROVER, Role.APPROVER)
    grant(db, PUBLISHER, Role.PUBLISHER)

    # --- submit with an attachment
    token = login(client, outbox, SUBMITTER)
    outbox.clear()
    data = _form(db)
    data["csrf_token"] = token
    r = client.post(
        "/submissions/new", data=data, files=[("files", ("brief.pdf", b"%PDF-1.4 test", "application/pdf"))]
    )
    assert r.status_code == 303, r.text
    sub = db.execute(select(Submission)).scalar_one()
    assert sub.status == Status.SUBMITTED.value
    assert sub.reference_no and sub.reference_no.startswith("SOL-")
    assert len(sub.active_attachments) == 1
    assert {c.email for c in sub.contacts} == {SUBMITTER, "olivia.owner@amentum.com", "chris.colead@amentum.com"}
    subjects = [m["subject"] for m in outbox]
    assert any("awaiting review" in s for s in subjects), subjects  # reviewer notified
    assert any(s.startswith("Received:") for s in subjects), subjects  # owners notified
    assert any(m["to"] == REVIEWER for m in outbox)

    detail = client.get(f"/submissions/{sub.id}")
    assert detail.status_code == 200
    assert "Cloud Cyber Range" in detail.text

    # --- a stranger cannot see it
    login(client, outbox, "nosy@amentum.com")
    assert client.get(f"/submissions/{sub.id}").status_code == 403

    # --- reviewer claims and requests updates
    token = login(client, outbox, REVIEWER)
    outbox.clear()
    assert _post_action(client, token, sub.id, "claim").status_code == 303
    db.expire_all()
    assert db.get(Submission, sub.id).status == Status.UNDER_REVIEW.value
    assert _post_action(client, token, sub.id, "request_updates", note="Please add contract values.").status_code == 303
    db.expire_all()
    assert db.get(Submission, sub.id).status == Status.UPDATES_REQUIRED.value
    assert any("updates required" in m["subject"] for m in outbox)
    assert any(m["to"] == "olivia.owner@amentum.com" for m in outbox)

    # --- submitter edits and resubmits
    token = login(client, outbox, SUBMITTER)
    outbox.clear()
    data = _form(db, current_pipeline="Program X recompete, $40M; Program Y, $12M", intent="resubmit")
    data["csrf_token"] = token
    r = client.post(f"/submissions/{sub.id}/edit", data=data)
    assert r.status_code == 303
    db.expire_all()
    sub = db.get(Submission, sub.id)
    assert sub.status == Status.UNDER_REVIEW.value
    assert sub.revision == 2
    assert any(m["to"] == REVIEWER and "Resubmitted" in m["subject"] for m in outbox)

    # --- reviewer completes; approver who is a contact is refused; real approver approves
    token = login(client, outbox, REVIEWER)
    assert _post_action(client, token, sub.id, "complete_review").status_code == 303
    db.expire_all()
    assert db.get(Submission, sub.id).status == Status.AWAITING_APPROVAL.value

    grant(db, "olivia.owner@amentum.com", Role.APPROVER)
    token = login(client, outbox, "olivia.owner@amentum.com")
    r = _post_action(client, token, sub.id, "approve")
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Submission, sub.id).status == Status.AWAITING_APPROVAL.value  # refused: separation of duties

    token = login(client, outbox, APPROVER)
    outbox.clear()
    assert _post_action(client, token, sub.id, "approve", note="Looks good").status_code == 303
    db.expire_all()
    sub = db.get(Submission, sub.id)
    assert sub.status == Status.APPROVED.value
    assert sub.approved_version_id is not None
    assert sub.versions[0].snapshot["offering_name"] == "Cloud Cyber Range"
    assert any(m["to"] == PUBLISHER for m in outbox)

    # --- publisher confirms readiness, exports package, publishes
    token = login(client, outbox, PUBLISHER)
    from app.models import PublishDestination

    dest_id = db.execute(select(PublishDestination.id)).scalars().first()
    assert _post_action(client, token, sub.id, "confirm_ready", publish_destination_id=str(dest_id)).status_code == 303
    db.expire_all()
    assert db.get(Submission, sub.id).status == Status.READY_TO_PUBLISH.value

    z = client.get(f"/submissions/{sub.id}/export.zip")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert {"offering.md", "offering.json", "approval.txt", "attachments/brief.pdf"} <= set(names)

    outbox.clear()
    assert (
        _post_action(
            client, token, sub.id, "publish", published_url="https://www.amentum.com/capabilities/cyber-range"
        ).status_code
        == 303
    )
    db.expire_all()
    sub = db.get(Submission, sub.id)
    assert sub.status == Status.PUBLISHED.value
    assert sub.next_review_due is not None
    assert len(sub.review_cycles) == 1
    assert any("Published:" in m["subject"] for m in outbox)

    # --- six-month review: force the cycle due and run the job
    cycle = sub.review_cycles[0]
    cycle.due_at = utcnow() + timedelta(days=3)
    db.commit()
    outbox.clear()
    run_review_cycles(db)
    assert any("Content review due" in m["subject"] for m in outbox)

    token = login(client, outbox, SUBMITTER)
    assert _post_action(client, token, sub.id, "confirm_review").status_code == 303
    db.expire_all()
    sub = db.get(Submission, sub.id)
    assert sub.review_cycles[0].closed_reason == "confirmed"
    assert len(sub.review_cycles) == 2

    # --- audit trail recorded every transition
    transitions = [e.to_status for e in sub.events if e.event_type == "transition"]
    assert transitions == [
        "submitted",
        "under_review",
        "updates_required",
        "under_review",
        "awaiting_approval",
        "approved",
        "ready_to_publish",
        "published",
    ]


def test_detail_page_forms_carry_csrf_token_and_actions_work_through_rendered_form(client, outbox, db):
    """Regression: macros imported without `with context` rendered an empty CSRF field (403 on every action)."""
    import re

    grant(db, REVIEWER, Role.REVIEWER)
    token = login(client, outbox, SUBMITTER)
    data = _form(db)
    data["csrf_token"] = token
    client.post("/submissions/new", data=data)
    sub = db.execute(select(Submission)).scalar_one()
    login(client, outbox, REVIEWER)
    page = client.get(f"/submissions/{sub.id}")
    assert page.status_code == 200
    assert 'name="csrf_token" value=""' not in page.text
    m = re.search(
        r'<form method="post" action="/submissions/\d+/action"[^>]*>\s*<input type="hidden" name="csrf_token" value="([^"]+)">\s*<input type="hidden" name="action" value="claim">',
        page.text,
    )
    assert m, "claim form with csrf token not found"
    r = client.post(f"/submissions/{sub.id}/action", data={"csrf_token": m.group(1), "action": "claim", "note": ""})
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Submission, sub.id).status == Status.UNDER_REVIEW.value


def test_incomplete_submission_is_rejected_with_errors(client, outbox, db):
    token = login(client, outbox, SUBMITTER)
    data = _form(db, offering_name="", capabilities=[], contact_email=[""], contact_name=[""])
    data["csrf_token"] = token
    r = client.post("/submissions/new", data=data)
    assert r.status_code == 400
    assert "Offering name is required" in r.text
    assert "between 1 and 3 capabilities" in r.text
    assert "Offering Owner" in r.text
    assert db.execute(select(Submission)).first() is None


def test_more_than_three_capabilities_rejected(client, outbox, db):
    token = login(client, outbox, SUBMITTER)
    caps = db.execute(select(Capability)).scalars().all()[:4]
    data = _form(db, capabilities=[str(c.id) for c in caps])
    data["csrf_token"] = token
    r = client.post("/submissions/new", data=data)
    assert r.status_code == 400
    assert "at most 3" in r.text


def test_draft_then_submit(client, outbox, db):
    token = login(client, outbox, SUBMITTER)
    data = _form(db, intent="draft", customer_challenge="")
    data["csrf_token"] = token
    r = client.post("/submissions/new", data=data)
    assert r.status_code == 303
    sub = db.execute(select(Submission)).scalar_one()
    assert sub.status == Status.DRAFT.value and sub.reference_no is None
    data = _form(db, intent="submit")
    data["csrf_token"] = token
    assert client.post(f"/submissions/{sub.id}/edit", data=data).status_code == 303
    db.expire_all()
    assert db.get(Submission, sub.id).status == Status.SUBMITTED.value


def test_reminders_sent_when_action_is_stale(client, outbox, db):
    grant(db, REVIEWER, Role.REVIEWER)
    token = login(client, outbox, SUBMITTER)
    data = _form(db)
    data["csrf_token"] = token
    client.post("/submissions/new", data=data)
    sub = db.execute(select(Submission)).scalar_one()
    sub.last_action_at = utcnow() - timedelta(days=10)
    db.commit()
    outbox.clear()
    if utcnow().weekday() >= 5:
        return  # job intentionally idle at weekends
    send_action_reminders(db)
    assert any("Reminder" in m["subject"] and m["to"] == REVIEWER for m in outbox), [m["subject"] for m in outbox]
    db.expire_all()
    assert any(e.event_type == "reminder_sent" for e in db.get(Submission, sub.id).events)
    outbox.clear()
    send_action_reminders(db)  # second run within the threshold: no duplicate
    assert outbox == []


def test_business_days():
    mon = utcnow().replace(year=2026, month=9, day=7, hour=9)  # Monday
    assert business_days_between(mon, mon + timedelta(days=7)) == 5
    assert business_days_between(mon, mon + timedelta(days=2)) == 2
    assert business_days_between(mon, mon) == 0


def test_comment_notifies_owners_and_reviewer(client, outbox, db):
    grant(db, REVIEWER, Role.REVIEWER)
    token = login(client, outbox, SUBMITTER)
    data = _form(db)
    data["csrf_token"] = token
    client.post("/submissions/new", data=data)
    sub = db.execute(select(Submission)).scalar_one()
    token = login(client, outbox, REVIEWER)
    outbox.clear()
    r = client.post(
        f"/submissions/{sub.id}/comments",
        data={"csrf_token": token, "body": "Can you add a case study?", "is_blocking": "1"},
    )
    assert r.status_code == 303
    tos = {m["to"] for m in outbox}
    assert {"olivia.owner@amentum.com", "chris.colead@amentum.com", SUBMITTER} <= tos
    assert REVIEWER not in tos
