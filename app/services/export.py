"""Publishing handoff package: a ZIP with Markdown, JSON, approval record and attachments."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime

from app.enums import DEPLOYMENT_LABELS, READINESS_LABELS, DeploymentStatus, ReadinessLevel
from app.models import Submission
from app.services.storage import get_storage


def _fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else ""


def snapshot(sub: Submission) -> dict:
    """Serialisable view of a submission, used for approval snapshots and offering.json."""
    return {
        "reference_no": sub.reference_no,
        "offering_name": sub.offering_name,
        "status": sub.status,
        "revision": sub.revision,
        "business_group": sub.business_group.name if sub.business_group else None,
        "cto_aware": sub.cto_aware,
        "customer_challenge": sub.customer_challenge,
        "technical_description": sub.technical_description,
        "key_benefits": sub.key_benefits,
        "readiness_level": sub.readiness_level,
        "readiness_programs": sub.readiness_programs,
        "deployment_status": sub.deployment_status,
        "deployment_detail": sub.deployment_detail,
        "additional_customers": sub.additional_customers,
        "current_pipeline": sub.current_pipeline,
        "resource_links_notes": sub.resource_links_notes,
        "contacts": [
            {"role": c.contact_role, "name": c.name, "email": c.email, "phone": c.phone} for c in sub.contacts
        ],
        "capabilities": [
            {
                "area": sc.capability.area.name,
                "capability": sc.capability.name,
                "other_text": sc.other_text,
            }
            for sc in sub.capabilities
        ],
        "attachments": [
            {
                "filename": a.original_filename,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "sha256": a.sha256,
            }
            for a in sub.active_attachments
        ],
        "submitted_at": _fmt(sub.submitted_at),
        "approved_at": _fmt(sub.approved_at),
        "published_at": _fmt(sub.published_at),
        "published_url": sub.published_url,
        "publish_destination": sub.publish_destination.name if sub.publish_destination else None,
    }


def offering_markdown(sub: Submission) -> str:
    snap = snapshot(sub)
    readiness = READINESS_LABELS.get(ReadinessLevel(sub.readiness_level), "") if sub.readiness_level else ""
    deployment = DEPLOYMENT_LABELS.get(DeploymentStatus(sub.deployment_status), "") if sub.deployment_status else ""
    lines = [
        f"# {sub.offering_name}",
        "",
        f"**Reference:** {sub.reference_no}  ",
        f"**Business Group:** {snap['business_group'] or ''}  ",
        f"**Revision:** {sub.revision}  ",
        "",
        "## Capability classification",
        "",
    ]
    for c in snap["capabilities"]:
        extra = f" – {c['other_text']}" if c["other_text"] else ""
        lines.append(f"- {c['area']}: {c['capability']}{extra}")
    lines += [
        "",
        "## Customer challenge",
        "",
        sub.customer_challenge,
        "",
        "## Technical solution",
        "",
        sub.technical_description,
        "",
        "## Key customer benefits",
        "",
        sub.key_benefits,
        "",
        "## Readiness and deployment",
        "",
        f"- **Level of readiness:** {readiness}",
        f"- **Programs / clients:** {sub.readiness_programs}",
        f"- **Deployed or proposed:** {deployment}",
        f"- **Detail:** {sub.deployment_detail}",
        f"- **Additional customers:** {sub.additional_customers}",
        f"- **Current pipeline:** {sub.current_pipeline}",
        "",
        "## Offering owners",
        "",
    ]
    for c in sub.contacts:
        lines.append(f"- {c.role_label}: {c.name} <{c.email}>" + (f" {c.phone}" if c.phone else ""))
    lines += ["", "## Supporting resources", "", sub.resource_links_notes or "", ""]
    for a in sub.active_attachments:
        lines.append(f"- attachments/{a.original_filename} ({a.size_bytes} bytes)")
    return "\n".join(lines) + "\n"


def approval_record(sub: Submission) -> str:
    approvals = [e for e in sub.events if e.event_type == "transition" and e.to_status == "approved"]
    out = [f"Reference: {sub.reference_no}", f"Offering: {sub.offering_name}", f"Revision: {sub.revision}", ""]
    for e in approvals:
        out.append(f"Approved by {e.actor_name or e.actor_email} <{e.actor_email}> at {_fmt(e.created_at)}")
        if e.note:
            out.append(f"  Note: {e.note}")
    if not approvals:
        out.append("No approval recorded on this revision.")
    return "\n".join(out) + "\n"


def build_package(sub: Submission) -> bytes:
    storage = get_storage()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("offering.md", offering_markdown(sub))
        z.writestr("offering.json", json.dumps(snapshot(sub), indent=2, default=str))
        z.writestr("approval.txt", approval_record(sub))
        for a in sub.active_attachments:
            try:
                z.writestr(f"attachments/{a.original_filename}", storage.read(a.blob_path))
            except Exception as exc:  # noqa: BLE001
                z.writestr(f"attachments/MISSING-{a.original_filename}.txt", f"Could not read attachment: {exc}")
    return buf.getvalue()
