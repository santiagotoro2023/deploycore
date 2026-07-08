"""deployment_health_checks: append-only health check history

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

health_status_enum = postgresql.ENUM("unknown", "healthy", "unreachable", name="health_status", create_type=False)


def upgrade() -> None:
    op.create_table(
        "deployment_health_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", health_status_enum, nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_deployment_health_checks_deployment_id_checked_at",
        "deployment_health_checks",
        ["deployment_id", "checked_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_health_checks_deployment_id_checked_at", table_name="deployment_health_checks")
    op.drop_table("deployment_health_checks")
