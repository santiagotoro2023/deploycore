"""deployment_templates: drop unused vlan_id

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-09

"""
import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Never actually applied anywhere in the provisioning pipeline (no
    # ESXi network adapter config ever read it); this environment's
    # networking is done entirely via dedicated port groups per VLAN, not
    # a VLAN tag set on the vNIC itself, so there was never a real use for
    # it. Dropped rather than left unused to stop confusing the form.
    op.drop_column("deployment_templates", "vlan_id")


def downgrade() -> None:
    op.add_column("deployment_templates", sa.Column("vlan_id", sa.Integer(), nullable=True))
