"""deployments, deployment_state_transitions, deployment_log_lines

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

ip_mode_enum = postgresql.ENUM("dhcp", "static", name="ip_mode")
deployment_state_enum = postgresql.ENUM(
    "pending", "creating_vm", "booting", "installing_os", "post_install", "configuring", "completed", "failed",
    name="deployment_state",
)
log_level_enum = postgresql.ENUM("info", "warn", "error", name="log_level")


def upgrade() -> None:
    ip_mode_enum.create(op.get_bind(), checkfirst=True)
    deployment_state_enum.create(op.get_bind(), checkfirst=True)
    log_level_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "deployments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deployment_templates.id"), nullable=False),
        sa.Column("hypervisor_host_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("hypervisor_hosts.id"), nullable=False),
        sa.Column("hostname", sa.String(255), nullable=False),
        sa.Column("ip_mode", sa.Enum("dhcp", "static", name="ip_mode", create_type=False), nullable=False),
        sa.Column("static_ip", sa.String(64), nullable=True),
        sa.Column("static_netmask", sa.String(64), nullable=True),
        sa.Column("static_gateway", sa.String(64), nullable=True),
        sa.Column("static_dns", postgresql.JSONB(), nullable=True),
        sa.Column(
            "state",
            sa.Enum(
                "pending", "creating_vm", "booting", "installing_os", "post_install", "configuring", "completed", "failed",
                name="deployment_state", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("callback_token", sa.String(64), nullable=False),
        sa.Column("callback_token_used", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("vm_moref", sa.String(255), nullable=True),
        sa.Column("answer_iso_remote_path", sa.String(1024), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_deployments_org_id", "deployments", ["org_id"])
    op.create_index("ix_deployments_callback_token", "deployments", ["callback_token"], unique=True)

    op.create_table(
        "deployment_state_transitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_state", sa.String(32), nullable=False),
        sa.Column("to_state", sa.String(32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
    )
    op.create_index("ix_deployment_state_transitions_deployment_id", "deployment_state_transitions", ["deployment_id"])

    op.create_table(
        "deployment_log_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("deployment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("level", sa.Enum("info", "warn", "error", name="log_level", create_type=False), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=False),
    )
    op.create_index("ix_deployment_log_lines_deployment_id_ts", "deployment_log_lines", ["deployment_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_deployment_log_lines_deployment_id_ts", table_name="deployment_log_lines")
    op.drop_table("deployment_log_lines")
    op.drop_index("ix_deployment_state_transitions_deployment_id", table_name="deployment_state_transitions")
    op.drop_table("deployment_state_transitions")
    op.drop_index("ix_deployments_callback_token", table_name="deployments")
    op.drop_index("ix_deployments_org_id", table_name="deployments")
    op.drop_table("deployments")
    log_level_enum.drop(op.get_bind(), checkfirst=True)
    deployment_state_enum.drop(op.get_bind(), checkfirst=True)
    ip_mode_enum.drop(op.get_bind(), checkfirst=True)
