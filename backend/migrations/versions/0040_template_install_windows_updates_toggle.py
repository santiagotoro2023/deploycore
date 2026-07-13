"""add deployment_templates.install_windows_updates toggle

Explicitly requested: a per-template (and per-deployment, via the
"Customize installation" override) checkbox to skip the Windows Update
post-install step entirely for deployments that need to be quick.
Previously this step always ran unconditionally. Defaults to true,
preserving existing behavior for every template that already exists.

Revision ID: 0040
Revises: 0039
Create Date: 2026-07-14

"""
from alembic import op
import sqlalchemy as sa

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployment_templates",
        sa.Column("install_windows_updates", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("deployment_templates", "install_windows_updates")
