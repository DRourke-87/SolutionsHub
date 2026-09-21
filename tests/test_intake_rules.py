"""Intake-form rules from the September 2026 business feedback: sensitive-data consent, word limits,
the RACI owner categories, the refreshed capability taxonomy and the five displayed stages."""

from __future__ import annotations

from sqlalchemy import select

from app.content import WORD_LIMITS
from app.enums import DISPLAY_STAGES, STAGE_LABELS, Stage, Status, stage_for
from app.models import Capability, CapabilityArea, Submission
from tests.conftest import login
from tests.test_workflow_e2e import SUBMITTER, _form


def test_submission_requires_sensitive_data_confirmation_and_records_it(client, outbox, db):
    token = login(client, outbox, SUBMITTER)
    data = _form(db)
    data.pop("sensitive_data_ack")
    data["csrf_token"] = token
    r = client.post("/submissions/new", data=data)
    assert r.status_code == 400
    assert "no sensitive information" in r.text
    assert db.execute(select(Submission)).first() is None

    data = _form(db)
    data["csrf_token"] = token
    assert client.post("/submissions/new", data=data).status_code == 303
    sub = db.execute(select(Submission)).scalar_one()
    assert sub.sensitive_data_acknowledged
    assert sub.sensitive_data_ack_by_email == SUBMITTER
    assert any(e.event_type == "sensitive_data_ack" for e in sub.events)


def test_sensitive_data_disclaimer_is_shown_on_the_form(client, outbox, db):
    login(client, outbox, SUBMITTER)
    page = client.get("/submissions/new")
    assert page.status_code == 200
    assert "Do not submit sensitive information" in page.text
    assert "No sensitive data in attachments" in page.text
    assert page.text.count("ITAR") >= 2  # top of the form and beside the attachments


def test_word_limits_are_enforced_on_submit(client, outbox, db):
    token = login(client, outbox, SUBMITTER)
    data = _form(db, customer_challenge=" ".join(["word"] * (WORD_LIMITS["customer_challenge"] + 1)))
    data["csrf_token"] = token
    r = client.post("/submissions/new", data=data)
    assert r.status_code == 400
    assert f"{WORD_LIMITS['customer_challenge']} words or fewer" in r.text
    assert db.execute(select(Submission)).first() is None


def test_supporting_files_and_pipeline_are_optional(client, outbox, db):
    token = login(client, outbox, SUBMITTER)
    data = _form(db, resource_links_notes="", current_pipeline="", additional_customers="")
    data["csrf_token"] = token
    r = client.post("/submissions/new", data=data)
    assert r.status_code == 303, r.text
    sub = db.execute(select(Submission)).scalar_one()
    assert sub.status == Status.SUBMITTED.value
    assert not sub.active_attachments


def test_owner_roles_are_the_raci_categories(client, outbox, db):
    token = login(client, outbox, SUBMITTER)
    page = client.get("/submissions/new")
    assert "Owner - Technical" in page.text and "Owner - Operations" in page.text
    assert "Co-lead" not in page.text and "Solution Architect" not in page.text

    data = _form(db)
    data["csrf_token"] = token
    assert client.post("/submissions/new", data=data).status_code == 303
    sub = db.execute(select(Submission)).scalar_one()
    assert {c.contact_role for c in sub.owners} == {"owner_technical", "owner_operations"}
    assert sub.has_owner


def test_capability_taxonomy_matches_the_website_and_other_stands_alone(db):
    areas = db.execute(select(CapabilityArea).order_by(CapabilityArea.sort_order)).scalars().all()
    assert [a.name for a in areas] == [
        "Mission Modernization & Sustainment",
        "Space Systems",
        "Digital Transformation",
        "Sustainability & Environment",
        "Advanced Energy Solutions",
        "Data Analytics and Cyber Solutions",
        "Other",
    ]
    space = next(a for a in areas if a.name == "Space Systems")
    assert len([c for c in space.capabilities if c.is_active]) == 6

    other = db.execute(select(Capability).where(Capability.code == "other")).scalar_one()
    assert other.area.name == "Other"
    data_cyber = next(a for a in areas if a.name == "Data Analytics and Cyber Solutions")
    assert "other" not in {c.code for c in data_cyber.capabilities}


def test_displayed_stages_are_the_five_business_stages():
    assert [STAGE_LABELS[s] for s in DISPLAY_STAGES] == [
        "Submitted",
        "Under Review",
        "Awaiting Edits",
        "Approved",
        "Published",
    ]
    assert stage_for(Status.AWAITING_APPROVAL) == Stage.UNDER_REVIEW
    assert stage_for(Status.UPDATES_REQUIRED) == Stage.AWAITING_EDITS
    assert stage_for(Status.READY_TO_PUBLISH) == Stage.APPROVED
    assert stage_for(Status.DRAFT) is None
