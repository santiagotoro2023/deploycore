"""deployment_templates.enable_rdp (on by default)

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-11

"""
import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_templates",
        sa.Column("enable_rdp", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("deployment_templates", "enable_rdp")
