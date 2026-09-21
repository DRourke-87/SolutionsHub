"""intake form feedback: differentiators, proposal flag, partnerships, sensitive-data consent, RACI owners

Revision ID: 3f1a92c47b08
Revises: bdc747c7de4d
Create Date: 2026-09-21 09:10:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3f1a92c47b08"
down_revision = "bdc747c7de4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Section 3: "Key Customer Benefit(s)" becomes "Key Differentiators"
    op.alter_column("submissions", "key_benefits", new_column_name="key_differentiators")

    # Section 3: "Currently deployed or proposed" becomes "Is this solution currently bid on a proposal?"
    # The free-text "for which program or customer" box is dropped as redundant.
    op.add_column("submissions", sa.Column("on_proposal", sa.Boolean(), nullable=True))
    op.execute(
        "UPDATE submissions SET on_proposal = TRUE WHERE deployment_status IN ('proposed', 'both')"
    )
    op.execute(
        "UPDATE submissions SET on_proposal = FALSE WHERE deployment_status IN ('deployed', 'neither')"
    )
    op.drop_column("submissions", "deployment_status")
    op.drop_column("submissions", "deployment_detail")

    # Section 4: relevant partnerships
    op.add_column("submissions", sa.Column("relevant_partnerships", sa.Text(), nullable=False, server_default=""))
    op.add_column("submissions", sa.Column("partnership_url", sa.String(length=1000), nullable=True))
    op.alter_column("submissions", "relevant_partnerships", server_default=None)

    # Recorded consent that no sensitive data was entered or attached
    op.add_column("submissions", sa.Column("sensitive_data_ack_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("submissions", sa.Column("sensitive_data_ack_by_email", sa.String(length=320), nullable=True))
    op.add_column("submissions", sa.Column("sensitive_data_ack_ip", sa.String(length=64), nullable=True))

    # Section 1: owner categories aligned to the RACI. Solution architects are technical owners;
    # co-leads / backups become operations owners.
    op.execute("UPDATE submission_contacts SET contact_role = 'owner_technical' WHERE contact_role = 'owner'")
    op.execute(
        "UPDATE submission_contacts SET contact_role = 'owner_technical' WHERE contact_role = 'solution_architect'"
    )
    op.execute("UPDATE submission_contacts SET contact_role = 'owner_operations' WHERE contact_role = 'co_lead'")


def downgrade() -> None:
    op.execute("UPDATE submission_contacts SET contact_role = 'owner' WHERE contact_role = 'owner_technical'")
    op.execute("UPDATE submission_contacts SET contact_role = 'co_lead' WHERE contact_role = 'owner_operations'")

    op.drop_column("submissions", "sensitive_data_ack_ip")
    op.drop_column("submissions", "sensitive_data_ack_by_email")
    op.drop_column("submissions", "sensitive_data_ack_at")

    op.drop_column("submissions", "partnership_url")
    op.drop_column("submissions", "relevant_partnerships")

    op.add_column("submissions", sa.Column("deployment_detail", sa.Text(), nullable=False, server_default=""))
    op.add_column("submissions", sa.Column("deployment_status", sa.String(length=20), nullable=True))
    op.execute("UPDATE submissions SET deployment_status = 'proposed' WHERE on_proposal IS TRUE")
    op.execute("UPDATE submissions SET deployment_status = 'neither' WHERE on_proposal IS FALSE")
    op.alter_column("submissions", "deployment_detail", server_default=None)
    op.drop_column("submissions", "on_proposal")

    op.alter_column("submissions", "key_differentiators", new_column_name="key_benefits")
