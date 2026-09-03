from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.db import utcnow
from app.enums import ContactRole, Status
from app.models import (
    Attachment,
    BusinessGroup,
    Capability,
    Submission,
    SubmissionCapability,
    SubmissionContact,
    SubmissionVersion,
)
from app.services.export import snapshot
from app.services.storage import get_storage
from app.workflow import mint_reference
from tests.conftest import login


def _offering(db, name: str, status: Status, cap_codes: list[str], challenge: str = "Challenge text") -> Submission:
    bg = db.execute(select(BusinessGroup)).scalars().first()
    sub = Submission(
        offering_name=name,
        status=status.value,
        business_group_id=bg.id,
        cto_aware=True,
        customer_challenge=challenge,
        technical_description="Solution",
        key_benefits="- Benefit",
        readiness_level="prototype",
        deployment_status="proposed",
        current_pipeline="x",
        created_by_email="owner@amentum.com",
        approved_at=utcnow() - timedelta(days=2),
        published_at=utcnow() - timedelta(days=1) if status == Status.PUBLISHED else None,
        published_url="https://www.amentum.com/x" if status == Status.PUBLISHED else None,
    )
    db.add(sub)
    db.flush()
    sub.reference_no = mint_reference(db)
    sub.contacts.append(
        SubmissionContact(contact_role=ContactRole.OWNER.value, name="Olivia Owner", email="owner@amentum.com")
    )
    for code in cap_codes:
        cap = db.execute(select(Capability).where(Capability.code == code)).scalar_one()
        sub.capabilities.append(SubmissionCapability(capability=cap))
    db.flush()
    db.refresh(sub)
    if status in (Status.APPROVED, Status.READY_TO_PUBLISH, Status.PUBLISHED):
        db.add(SubmissionVersion(submission_id=sub.id, revision=1, snapshot=snapshot(sub)))
    db.commit()
    return sub


def test_catalogue_is_landing_page_and_shows_only_approved_content(client, outbox, db):
    pub = _offering(db, "Published Thing", Status.PUBLISHED, ["dt_cloud"])
    _offering(db, "Approved Thing", Status.APPROVED, ["space_ports"])
    _offering(db, "Still In Review", Status.UNDER_REVIEW, ["dt_cloud"])
    login(client, outbox, "stranger@amentumcms.com")
    r = client.get("/")
    assert r.status_code == 200
    assert "Published Thing" in r.text and "Approved Thing" in r.text
    assert "Still In Review" not in r.text
    assert f"/catalogue/{pub.id}" in r.text
    assert "Solutions &amp; Offerings" in r.text


def test_catalogue_filters_by_area_business_group_and_search(client, outbox, db):
    _offering(db, "Cloud Thing", Status.PUBLISHED, ["dt_cloud"], challenge="hyperscale migration")
    _offering(db, "Space Thing", Status.PUBLISHED, ["space_ports"], challenge="launch cadence")
    login(client, outbox, "someone@amentum.com")
    space_area = db.execute(select(Capability).where(Capability.code == "space_ports")).scalar_one().area_id
    r = client.get(f"/catalogue?area={space_area}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "Space Thing" in r.text and "Cloud Thing" not in r.text
    assert "<html" not in r.text  # partial response for HTMX
    r = client.get("/catalogue?q=hyperscale")
    assert "Cloud Thing" in r.text and "Space Thing" not in r.text
    r = client.get("/catalogue?readiness=conceptual")
    assert "No offerings match" in r.text
    # HTML forms send empty strings for unselected filters; these must not 422
    assert (
        client.get("/catalogue?q=&area=&business_group_id=&readiness=", headers={"HX-Request": "true"}).status_code
        == 200
    )
    assert client.get("/submissions?status=&q=&business_group_id=").status_code == 200


def test_catalogue_detail_visible_to_any_user_and_hides_unapproved(client, outbox, db):
    pub = _offering(db, "Detail Thing", Status.PUBLISHED, ["ae_consulting"])
    draft = _offering(db, "Hidden Thing", Status.SUBMITTED, ["ae_consulting"])
    login(client, outbox, "reader@global.amentum.com")
    r = client.get(f"/catalogue/{pub.id}")
    assert r.status_code == 200
    assert "Detail Thing" in r.text and "Olivia Owner" in r.text and "View published page" in r.text
    assert "Open full record" not in r.text  # reader has no rights to the working record
    assert client.get(f"/catalogue/{draft.id}").status_code == 404
    assert client.get(f"/submissions/{pub.id}").status_code == 403  # workflow view still restricted


def test_catalogue_attachment_download_allowed_for_published(client, outbox, db):
    pub = _offering(db, "File Thing", Status.PUBLISHED, ["dt_cloud"])
    get_storage().save("submissions/x/1/brief.txt", __import__("io").BytesIO(b"hello"), "text/plain")
    att = Attachment(
        submission_id=pub.id,
        blob_path="submissions/x/1/brief.txt",
        original_filename="brief.txt",
        content_type="text/plain",
        size_bytes=5,
        uploaded_by_email="owner@amentum.com",
    )
    db.add(att)
    db.commit()
    login(client, outbox, "reader@amentum.com")
    r = client.get(f"/submissions/{pub.id}/attachments/{att.id}/download")
    assert r.status_code == 200 and r.content == b"hello"


def test_dashboard_moved_to_my_work(client, outbox):
    login(client, outbox, "worker@amentum.com")
    r = client.get("/dashboard")
    assert r.status_code == 200 and "My work" in r.text
