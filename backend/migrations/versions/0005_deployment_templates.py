"""deployment_templates

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

domain_join_timing_enum = postgresql.ENUM("answer_file", "post_install", name="domain_join_timing")


def upgrade() -> None:
    domain_join_timing_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "deployment_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("iso_asset_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("iso_assets.id"), nullable=True),
        sa.Column("disk_layout_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("disk_layouts.id"), nullable=False),
        sa.Column("cpu_count", sa.Integer(), nullable=False),
        sa.Column("ram_mb", sa.Integer(), nullable=False),
        sa.Column("disk_size_gb", sa.Integer(), nullable=False),
        sa.Column("network_name", sa.String(255), nullable=False),
        sa.Column("vlan_id", sa.Integer(), nullable=True),
        sa.Column("locale", sa.String(20), nullable=False, server_default="en-US"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="UTC"),
        sa.Column("keyboard_layout", sa.String(20), nullable=False, server_default="en-US"),
        sa.Column("local_admin_password_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("domain_join_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("domain_fqdn", sa.String(255), nullable=True),
        sa.Column("domain_join_account", sa.String(255), nullable=True),
        sa.Column("domain_join_credential_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("domain_target_ou", sa.String(512), nullable=True),
        sa.Column(
            "domain_join_timing",
            sa.Enum("answer_file", "post_install", name="domain_join_timing", create_type=False),
            nullable=False,
            server_default="answer_file",
        ),
        sa.Column("windows_features", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("post_install_scripts", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deployment_templates_org_id", "deployment_templates", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_deployment_templates_org_id", table_name="deployment_templates")
    op.drop_table("deployment_templates")
    domain_join_timing_enum.drop(op.get_bind(), checkfirst=True)
