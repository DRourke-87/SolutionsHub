"""Idempotent reference-data seeding. Safe to run on every deploy."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BusinessGroup, Capability, CapabilityArea, PublishDestination

CAPABILITY_TAXONOMY: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Mission Modernization & Sustainment",
        [
            ("mms_logistics", "Logistics and Supply Chain"),
            ("mms_systems_eng", "Systems Engineering & Sustainment"),
            ("mms_test_training", "Advanced Test and Training"),
            ("mms_rdte", "RDT&E"),
            ("mms_intel_infra", "Intelligence Infrastructure"),
        ],
    ),
    (
        "Space Systems",
        [
            ("space_ground", "Ground Systems"),
            ("space_ports", "Space Ports"),
            ("space_orbital", "Orbital Operations"),
        ],
    ),
    (
        "Digital Transformation",
        [
            ("dt_software", "Software Development"),
            ("dt_critical_infra", "Critical Digital Infrastructure"),
            ("dt_digital_eng", "Digital Engineering"),
            ("dt_enterprise_it", "Enterprise IT"),
            ("dt_it_cyber", "IT Cybersecurity"),
            ("dt_cloud", "Cloud"),
        ],
    ),
    (
        "Sustainability & Environment",
        [
            ("se_remediation", "Environmental Remediation & Decommissioning"),
            ("se_consulting", "Environmental Consulting"),
            ("se_regulatory", "Regulatory Compliance, Permitting, Licensing"),
        ],
    ),
    (
        "Advanced Energy",
        [
            ("ae_nuclear_eng", "Nuclear Engineering & Design"),
            ("ae_regulatory", "Regulatory, Site Licensing & Permitting"),
            ("ae_consulting", "Energy Consulting"),
            ("ae_research", "Research, Lab and Test Bed Operations"),
            ("ae_lifecycle", "Nuclear Energy Lifecycle"),
        ],
    ),
    (
        "Data Analytics and Cyber",
        [
            ("dac_ai_intel", "AI-source Intelligence Collection & Analytics"),
            ("dac_cyber_monitoring", "Cyber Monitoring & Threat Analytics"),
            ("dac_cyber_training", "Cyber Training"),
            ("dac_cyber_ops", "Offensive/Defensive Cyber Operations"),
            ("dac_im_comms", "Advanced IM/Communications"),
            ("other", "Other (please specify)"),
        ],
    ),
]

# Placeholder list: the business will supply the definitive Business Groups. Editable in Admin.
DEFAULT_BUSINESS_GROUPS = [
    "Digital Solutions",
    "Global Engineering Solutions",
    "Corporate / Enterprise Functions",
]

DEFAULT_PUBLISH_DESTINATIONS = [
    ("Amentum.com – Our Capabilities", "https://www.amentum.com/"),
    ("Internal Solutions Catalogue (SharePoint)", None),
]


def seed_reference_data(db: Session) -> dict[str, int]:
    created = {"areas": 0, "capabilities": 0, "business_groups": 0, "destinations": 0}

    for a_idx, (area_name, caps) in enumerate(CAPABILITY_TAXONOMY):
        area = db.execute(select(CapabilityArea).where(CapabilityArea.name == area_name)).scalar_one_or_none()
        if area is None:
            area = CapabilityArea(name=area_name, sort_order=a_idx)
            db.add(area)
            db.flush()
            created["areas"] += 1
        for c_idx, (code, name) in enumerate(caps):
            cap = db.execute(select(Capability).where(Capability.code == code)).scalar_one_or_none()
            if cap is None:
                db.add(Capability(area_id=area.id, code=code, name=name, sort_order=c_idx))
                created["capabilities"] += 1

    existing_groups = {g.name for g in db.execute(select(BusinessGroup)).scalars()}
    if not existing_groups:
        for i, name in enumerate(DEFAULT_BUSINESS_GROUPS):
            db.add(BusinessGroup(name=name, sort_order=i))
            created["business_groups"] += 1

    existing_dest = {d.name for d in db.execute(select(PublishDestination)).scalars()}
    if not existing_dest:
        for name, url in DEFAULT_PUBLISH_DESTINATIONS:
            db.add(PublishDestination(name=name, base_url=url))
            created["destinations"] += 1

    db.commit()
    return created


def main() -> None:  # pragma: no cover - CLI entry
    from app.db import get_sessionmaker

    with get_sessionmaker()() as db:
        print("seeded:", seed_reference_data(db))


if __name__ == "__main__":  # pragma: no cover
    main()
