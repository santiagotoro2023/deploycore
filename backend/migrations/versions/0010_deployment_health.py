"""deployments: last_health_status, last_health_checked_at

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

health_status_enum = postgresql.ENUM("unknown", "healthy", "unreachable", name="health_status", create_type=False)


def upgrade() -> None:
    health_status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "deployments",
        sa.Column("last_health_status", health_status_enum, nullable=False, server_default="unknown"),
    )
    op.add_column("deployments", sa.Column("last_health_checked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("deployments", "last_health_checked_at")
    op.drop_column("deployments", "last_health_status")
    health_status_enum.drop(op.get_bind(), checkfirst=True)
