"""hypervisor_hosts

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

hypervisor_type_enum = postgresql.ENUM("esxi", "proxmox", name="hypervisor_type", create_type=False)
connection_status_enum = postgresql.ENUM("unknown", "ok", "failed", name="connection_status", create_type=False)


def upgrade() -> None:
    hypervisor_type_enum.create(op.get_bind(), checkfirst=True)
    connection_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "hypervisor_hosts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "type",
            hypervisor_type_enum,
            nullable=False,
        ),
        sa.Column("api_endpoint", sa.String(255), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("credential_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("tls_verify", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_datastore", sa.String(255), nullable=True),
        sa.Column("default_network", sa.String(255), nullable=True),
        sa.Column(
            "last_test_status",
            connection_status_enum,
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_hypervisor_hosts_org_id", "hypervisor_hosts", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_hypervisor_hosts_org_id", table_name="hypervisor_hosts")
    op.drop_table("hypervisor_hosts")
    connection_status_enum.drop(op.get_bind(), checkfirst=True)
    hypervisor_type_enum.drop(op.get_bind(), checkfirst=True)
