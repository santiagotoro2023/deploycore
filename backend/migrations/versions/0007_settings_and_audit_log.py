"""settings, audit_logs

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

setting_scope_enum = postgresql.ENUM("global", "org", "template", "deployment", name="setting_scope")


def upgrade() -> None:
    setting_scope_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "scope",
            sa.Enum("global", "org", "template", "deployment", name="setting_scope", create_type=False),
            nullable=False,
        ),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deployment_templates.id", ondelete="CASCADE"), nullable=True),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deployments.id", ondelete="CASCADE"), nullable=True),
        sa.Column("key", sa.String(255), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("scope", "org_id", "template_id", "deployment_id", "key", name="uq_setting_scope_key"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(255), nullable=False),
        sa.Column("target_type", sa.String(255), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_org_id", "audit_logs", ["org_id"])
    op.create_index("ix_audit_logs_occurred_at", "audit_logs", ["occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_occurred_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_org_id", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("settings")
    setting_scope_enum.drop(op.get_bind(), checkfirst=True)
