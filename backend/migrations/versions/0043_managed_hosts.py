"""managed_hosts (Remote Management) + app_assets.is_remote_agent

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-16

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_hosts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "deployment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deployments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("enroll_token", sa.String(64), nullable=False),
        sa.Column("enrolled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rustdesk_id", sa.String(64), nullable=True),
        sa.Column("rustdesk_key_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_managed_hosts_org_id", "managed_hosts", ["org_id"])
    op.create_unique_constraint("uq_managed_hosts_enroll_token", "managed_hosts", ["enroll_token"])

    # Marks the one (seeded, see services/remote_agent.py) global AppAsset
    # that is the DeployCore Remote Management Agent installer, so
    # worker/tasks/provision.py can special-case it: unlike every other
    # app install, this one gets a live, per-deployment enrollment token
    # injected into its install_args at deploy time (the same
    # deployment.app_asset_access_token-style pattern already used for
    # the download URL itself), because a single static install_args
    # string on the template attachment can't carry a value that has to
    # be different for every deployment.
    op.add_column("app_assets", sa.Column("is_remote_agent", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("app_assets", "is_remote_agent")
    op.drop_constraint("uq_managed_hosts_enroll_token", "managed_hosts", type_="unique")
    op.drop_index("ix_managed_hosts_org_id", table_name="managed_hosts")
    op.drop_table("managed_hosts")
