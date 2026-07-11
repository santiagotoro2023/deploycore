"""teams_config, notification_templates, teams_on_* preferences

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-12

"""
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

# Exactly the hardcoded strings every call site used before this
# migration (deployments.py, provision.py, maintenance.py) - seeded so
# behavior is identical until an operator edits one via the new
# notification-templates settings panel. {hostname}/{error}/{checked_at}
# are the only placeholders those call sites ever had data for; see
# services/notifications.py's EVENT_CONTEXT_FIELDS for the authoritative
# list per event type.
_DEFAULT_TEMPLATES = [
    {
        "event_type": "start",
        "email_subject": "Deployment {hostname} started",
        "email_body": "Deployment {hostname} has started provisioning.",
        "teams_message": "Deployment {hostname} has started provisioning.",
    },
    {
        "event_type": "complete",
        "email_subject": "Deployment {hostname} completed",
        "email_body": "Deployment {hostname} completed successfully.",
        "teams_message": "Deployment {hostname} completed successfully.",
    },
    {
        "event_type": "failed",
        "email_subject": "Deployment {hostname} failed",
        "email_body": "Deployment {hostname} failed: {error}",
        "teams_message": "Deployment {hostname} failed: {error}",
    },
    {
        "event_type": "health_degraded",
        "email_subject": "Deployment {hostname} became unreachable",
        "email_body": "Deployment {hostname} was healthy and is now unreachable as of {checked_at}.",
        "teams_message": "Deployment {hostname} was healthy and is now unreachable as of {checked_at}.",
    },
]

_notification_templates = sa.table(
    "notification_templates",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("event_type", sa.String),
    sa.column("email_subject", sa.String),
    sa.column("email_body", sa.Text),
    sa.column("teams_message", sa.Text),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    op.create_table(
        "teams_config",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.String(255), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=False),
        sa.Column("client_secret_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("teams_app_id", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "notification_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("email_subject", sa.String(255), nullable=False),
        sa.Column("email_body", sa.Text(), nullable=False),
        sa.Column("teams_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_unique_constraint("uq_notification_templates_event_type", "notification_templates", ["event_type"])

    now = datetime.now(timezone.utc)
    op.bulk_insert(
        _notification_templates,
        [
            {
                "id": uuid.uuid4(),
                "created_at": now,
                "updated_at": now,
                **row,
            }
            for row in _DEFAULT_TEMPLATES
        ],
    )

    op.add_column(
        "notification_preferences",
        sa.Column("teams_on_start", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("teams_on_complete", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("teams_on_failed", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "notification_preferences",
        sa.Column("teams_on_health_degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("notification_preferences", "teams_on_health_degraded")
    op.drop_column("notification_preferences", "teams_on_failed")
    op.drop_column("notification_preferences", "teams_on_complete")
    op.drop_column("notification_preferences", "teams_on_start")
    op.drop_table("notification_templates")
    op.drop_table("teams_config")
