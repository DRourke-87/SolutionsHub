"""Idempotent reference-data seeding. Safe to run on every deploy."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BusinessGroup, Capability, CapabilityArea, PublishDestination

# The capability areas and offerings published on amentum.com. "Other" is its own area so that it
# sorts last in the form (question 11) rather than sitting under Data Analytics and Cyber Solutions.
CAPABILITY_TAXONOMY: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Mission Modernization & Sustainment",
        [
            ("mms_rdte", "Research Development, Test and Evaluation"),
            ("mms_c5isr", "C5ISR Systems Engineering & Sustainment"),
            ("mms_uas", "UAS Engineering & Sustainment"),
            ("mms_aviation", "Aviation Engineering & Sustainment"),
            ("mms_land_vehicles", "Land Vehicles & Equipment Sustainment"),
            ("mms_naval_eng", "Naval Engineering & Sustainment"),
            ("mms_naval_deterrent", "Naval Deterrent Sustainment"),
            ("mms_test_training", "Advanced Test, Training, & Aerial Systems"),
            ("mms_logistics", "Global Logistics & Supply Chain Management"),
            ("mms_intel_infra", "Intelligence Infrastructure Solutions"),
            ("mms_nuclear_security", "Nuclear Security and Deterrence"),
            ("mms_medical_disaster", "Medical and Disaster Response"),
        ],
    ),
    (
        "Space Systems",
        [
            ("space_ground", "Ground Systems"),
            ("space_ports", "Spaceports"),
            ("space_hardware", "Spaceflight Hardware"),
            ("space_orbital", "Orbit Operations"),
            ("space_exploration", "Exploration Science"),
            ("space_payloads", "Satellite Payloads"),
        ],
    ),
    (
        "Digital Transformation",
        [
            ("dt_software", "Software Development & Engineering"),
            ("dt_information_analytics", "Information Analytics"),
            ("dt_critical_infra", "Critical Infrastructure and Advanced Networks"),
            ("dt_it_cyber", "Cybersecurity"),
            ("dt_digital_eng", "Digital Engineering"),
            ("dt_cloud", "Cloud"),
            ("dt_agile", "Agile Delivery Process"),
        ],
    ),
    (
        "Sustainability & Environment",
        [
            ("se_remediation", "Environmental Remediation & Decommissioning"),
            ("se_site_assessment", "Site Assessment & Characterization"),
            ("se_consulting", "Environmental Consulting"),
            ("se_risk_assessment", "Environmental Risk Assessment"),
            ("se_regulatory", "Environmental Regulatory Compliance, Permitting and Licensing"),
            ("se_pfas", "Eradication of Emerging Contaminants (PFAS)"),
            ("se_restoration", "Environmental Site Restoration and Reuse"),
            ("se_radwaste", "Radioactive Waste Management and Radiation Protection"),
        ],
    ),
    (
        "Advanced Energy Solutions",
        [
            ("ae_renewable", "Renewable Energy Solutions"),
            ("ae_consulting", "Energy Consulting"),
            ("ae_nuclear_eng", "Nuclear Engineering & Design"),
            ("ae_commissioning", "Commissioning, Operational Support and Life Extension"),
            ("ae_regulatory", "Regulatory, Site Licensing and Permitting"),
            ("ae_research", "Research, Laboratory and Energy Test Bed Operations"),
        ],
    ),
    (
        "Data Analytics and Cyber Solutions",
        [
            ("dac_ai_intel", "All-Source Intelligence Collection & Analytics"),
            ("dac_counter_intel", "Counter Intelligence Solutions"),
            ("dac_cyber_monitoring", "Continuous Cyber Monitoring & Threat Analytics"),
            ("dac_cyber_ops", "Offensive / Defensive Cyber Operations"),
            ("dac_cyber_training", "Full-spectrum Cyber Training"),
            ("dac_im_comms", "Advanced Communication Solutions"),
            ("dac_managed_bandwidth", "Managed Bandwidth & Secure Network Solutions"),
            ("dac_biometrics", "Integrated Biometrics"),
            ("dac_business_analytics", "Business Process Analytics"),
        ],
    ),
    (
        "Other",
        [
            ("other", "Other (please specify)"),
        ],
    ),
]

# Areas renamed in place so that existing submissions keep their classification.
AREA_RENAMES = {
    "Advanced Energy": "Advanced Energy Solutions",
    "Data Analytics and Cyber": "Data Analytics and Cyber Solutions",
}

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
    created = {"areas": 0, "capabilities": 0, "business_groups": 0, "destinations": 0, "retired": 0}

    for old_name, new_name in AREA_RENAMES.items():
        area = db.execute(select(CapabilityArea).where(CapabilityArea.name == old_name)).scalar_one_or_none()
        target = db.execute(select(CapabilityArea).where(CapabilityArea.name == new_name)).scalar_one_or_none()
        if area is not None and target is None:
            area.name = new_name
    db.flush()

    current_codes: set[str] = set()
    for a_idx, (area_name, caps) in enumerate(CAPABILITY_TAXONOMY):
        area = db.execute(select(CapabilityArea).where(CapabilityArea.name == area_name)).scalar_one_or_none()
        if area is None:
            area = CapabilityArea(name=area_name, sort_order=a_idx)
            db.add(area)
            db.flush()
            created["areas"] += 1
        else:
            area.sort_order = a_idx
        for c_idx, (code, name) in enumerate(caps):
            current_codes.add(code)
            cap = db.execute(select(Capability).where(Capability.code == code)).scalar_one_or_none()
            if cap is None:
                db.add(Capability(area_id=area.id, code=code, name=name, sort_order=c_idx))
                created["capabilities"] += 1
            else:
                # Keep the row (submissions reference it) but bring its wording and placement up to date.
                cap.area_id = area.id
                cap.name = name
                cap.sort_order = c_idx
                cap.is_active = True

    # Capabilities dropped from the taxonomy are hidden rather than deleted: historic submissions still
    # point at them, and the admin reference page can bring one back if it is needed again.
    for cap in db.execute(select(Capability).where(Capability.code.notin_(current_codes))).scalars():
        if cap.is_active:
            cap.is_active = False
            created["retired"] += 1

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
