"""deployments.overrides_encrypted ("Customize installation")

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-13

"""
import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column("overrides_encrypted", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployments", "overrides_encrypted")
