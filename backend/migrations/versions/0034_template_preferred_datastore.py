"""deployment_templates.preferred_datastore

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-14

"""
import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_templates",
        sa.Column("preferred_datastore", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployment_templates", "preferred_datastore")
