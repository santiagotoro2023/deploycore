"""deployments.guest_reported_ip

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-11

"""
import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("guest_reported_ip", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("deployments", "guest_reported_ip")
