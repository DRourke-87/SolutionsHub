"""Populate a *non-production* database with sample approved/published offerings so the catalogue can be
demonstrated. Idempotent: skips offerings whose name already exists.

    python -m scripts.seed_demo
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.config import get_settings
from app.db import get_sessionmaker, utcnow
from app.enums import ContactRole, EventType, Status
from app.models import (
    BusinessGroup,
    Capability,
    Submission,
    SubmissionCapability,
    SubmissionContact,
    SubmissionVersion,
    WorkflowEvent,
)
from app.services.export import snapshot
from app.workflow import mint_reference

DEMO = [
    dict(
        name="Autonomous Range Sensor Network",
        caps=["mms_test_training", "dt_cloud"],
        challenge="Test and training ranges lack persistent, low-cost sensing across large, remote areas, so instrumentation gaps slow test campaigns and inflate cost.",
        solution="A mesh of solar-powered edge sensors with LoRa backhaul feeding a cloud analytics pipeline. Infrastructure-as-code templates deploy the ingest, storage and dashboard tiers in under a day.",
        benefits="- Persistent coverage of the whole range, not just instrumented pads\n- About 60% lower cost than fibre-connected sensor towers\n- Deployable in days by a two-person crew",
        readiness="test_phase",
        deployment="proposed",
        status=Status.PUBLISHED,
        days_ago=12,
        url="https://www.amentum.com/capabilities/",
        owner=("Olivia Owner", "olivia.owner@amentum.com"),
        group=0,
    ),
    dict(
        name="Orbital Debris Conjunction Service",
        caps=["space_orbital", "dac_ai_intel"],
        challenge="Operators receive thousands of conjunction warnings a month with little context on which ones genuinely warrant a manoeuvre.",
        solution="Machine-learning triage of conjunction data messages combined with our flight-dynamics heritage to rank risk, recommend manoeuvres and feed the operator's planning tools through a REST API.",
        benefits="- Cuts false-positive manoeuvre planning by roughly 70%\n- Integrates with existing ground software in weeks\n- Auditable recommendations with full provenance",
        readiness="single_deployment",
        deployment="deployed",
        status=Status.PUBLISHED,
        days_ago=40,
        url="https://www.amentum.com/capabilities/",
        owner=("Marcus Lee", "marcus.lee@global.amentum.com"),
        group=1,
    ),
    dict(
        name="Nuclear Site Licensing Accelerator",
        caps=["ae_regulatory", "se_regulatory"],
        challenge="New nuclear and advanced-reactor projects spend years assembling licensing evidence from disconnected engineering, environmental and safety-case sources.",
        solution="A structured evidence library and workflow that maps every regulatory requirement to controlled documents, with automated gap reports and reviewer sign-off, built on our licensing playbooks from UK and US programmes.",
        benefits="- Licensing-basis gap analysis in weeks rather than months\n- One traceable evidence chain for regulators and auditors\n- Reusable across reactor technologies and jurisdictions",
        readiness="multi_client_deployment",
        deployment="both",
        status=Status.PUBLISHED,
        days_ago=75,
        url="https://www.amentum.com/capabilities/",
        owner=("Priya Natarajan", "priya.natarajan@amentumcms.com"),
        group=1,
    ),
    dict(
        name="Cyber Range as a Service",
        caps=["dac_cyber_training", "dt_cloud", "dt_it_cyber"],
        challenge="Defence and critical-infrastructure customers cannot exercise cyber teams against realistic replicas of their own operational networks without weeks of manual environment building.",
        solution="On-demand, cloud-hosted cyber ranges generated from network blueprints, with scripted adversary emulation, scoring and after-action reporting. Delivered under our existing cloud security authorisations.",
        benefits="- Stand up a realistic range in hours, tear down in minutes\n- Pay for exercise time, not idle infrastructure\n- Objective team scoring aligned to recognised frameworks",
        readiness="prototype",
        deployment="proposed",
        status=Status.APPROVED,
        days_ago=3,
        url=None,
        owner=("Dana Whitfield", "dana.whitfield@amentum.com"),
        group=0,
    ),
    dict(
        name="Grid Resilience Digital Twin",
        caps=["dt_digital_eng", "ae_consulting"],
        challenge="Transmission operators need to understand how extreme weather, cyber events and renewable intermittency interact before they invest in hardening.",
        solution="A physics-informed digital twin of the transmission network that ingests SCADA, weather and asset-condition data to run thousands of what-if scenarios, prioritising investments by avoided outage minutes.",
        benefits="- Quantified resilience business cases for regulators\n- Reuses operator data already collected\n- Scenario runs in minutes on commodity cloud",
        readiness="single_deployment",
        deployment="deployed",
        status=Status.PUBLISHED,
        days_ago=110,
        url="https://www.amentum.com/capabilities/",
        owner=("Tom Adeyemi", "tom.adeyemi@global.amentum.com"),
        group=1,
    ),
    dict(
        name="Environmental Remediation Robotics",
        caps=["se_remediation", "mms_rdte"],
        challenge="Characterising and remediating legacy contaminated facilities exposes workers to hazards and consumes most of a project's schedule in manual survey work.",
        solution="Remotely operated and semi-autonomous platforms carrying radiological and chemical sensors, with survey data flowing into a 3D site model that plans and verifies remediation passes.",
        benefits="- Removes people from the highest-dose tasks\n- Survey-to-plan cycle time cut by half on pilot sites\n- Verifiable, auditable clearance records",
        readiness="test_phase",
        deployment="deployed",
        status=Status.READY_TO_PUBLISH,
        days_ago=6,
        url=None,
        owner=("Grace Okafor", "grace.okafor@amentum.com"),
        group=1,
    ),
    dict(
        name="Spaceport Launch Operations Suite",
        caps=["space_ports", "mms_logistics"],
        challenge="Emerging spaceports run launch campaigns on spreadsheets and email, making range safety coordination, logistics and go/no-go decisions slow and error-prone.",
        solution="An integrated operations platform covering campaign scheduling, range safety checklists, propellant and logistics tracking, and a real-time launch readiness board, drawn from our launch-site operations experience.",
        benefits="- One source of truth for every launch campaign\n- Digital range-safety sign-offs with full audit trail\n- Configurable for government and commercial sites",
        readiness="conceptual",
        deployment="proposed",
        status=Status.PUBLISHED,
        days_ago=200,
        url="https://www.amentum.com/capabilities/",
        owner=("Elena Rossi", "elena.rossi@amentum.com"),
        group=0,
    ),
]


def main() -> None:
    settings = get_settings()
    if settings.is_prod:
        raise SystemExit("Refusing to seed demo data into a production environment.")
    now = utcnow()
    with get_sessionmaker()() as db:
        groups = db.execute(select(BusinessGroup).order_by(BusinessGroup.sort_order)).scalars().all()
        caps = {c.code: c for c in db.execute(select(Capability)).scalars()}
        created = 0
        for d in DEMO:
            if db.execute(select(Submission.id).where(Submission.offering_name == d["name"])).first():
                continue
            when = now - timedelta(days=d["days_ago"])
            sub = Submission(
                offering_name=d["name"],
                status=d["status"].value,
                business_group_id=groups[d["group"] % len(groups)].id if groups else None,
                cto_aware=True,
                customer_challenge=d["challenge"],
                technical_description=d["solution"],
                key_benefits=d["benefits"],
                readiness_level=d["readiness"],
                readiness_programs="Demonstration data",
                deployment_status=d["deployment"],
                deployment_detail="Demonstration data",
                current_pipeline="Demonstration data",
                resource_links_notes="https://www.amentum.com/",
                created_by_email=d["owner"][1],
                submitted_at=when - timedelta(days=20),
                review_completed_at=when - timedelta(days=10),
                approved_at=when - timedelta(days=5),
                published_at=when if d["status"] == Status.PUBLISHED else None,
                published_url=d["url"],
                next_review_due=when + timedelta(days=180) if d["status"] == Status.PUBLISHED else None,
                last_action_at=when,
                created_at=when - timedelta(days=21),
                revision=1,
            )
            db.add(sub)
            db.flush()
            sub.reference_no = mint_reference(db)
            sub.contacts.append(
                SubmissionContact(contact_role=ContactRole.RECORDER.value, name=d["owner"][0], email=d["owner"][1])
            )
            sub.contacts.append(
                SubmissionContact(contact_role=ContactRole.OWNER.value, name=d["owner"][0], email=d["owner"][1])
            )
            for code in d["caps"]:
                if code in caps:
                    sub.capabilities.append(SubmissionCapability(capability=caps[code]))
            db.flush()
            db.refresh(sub)
            ev = WorkflowEvent(
                submission_id=sub.id,
                revision=1,
                event_type=EventType.TRANSITION.value,
                from_status=Status.AWAITING_APPROVAL.value,
                to_status=Status.APPROVED.value,
                actor_email="demo.approver@amentum.com",
                actor_name="Demo Approver",
                note="Demo data",
                created_at=when - timedelta(days=5),
            )
            db.add(ev)
            db.flush()
            db.add(
                SubmissionVersion(
                    submission_id=sub.id,
                    revision=1,
                    snapshot=snapshot(sub),
                    approval_event_id=ev.id,
                    created_at=when - timedelta(days=5),
                )
            )
            created += 1
        db.commit()
        print(f"demo offerings created: {created}")


if __name__ == "__main__":
    main()
