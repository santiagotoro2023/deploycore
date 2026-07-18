"""Native Remote Management agent: drop RustDesk fields, add agent_key

rustdesk_id/rustdesk_key_encrypted don't carry over - the native protocol
(see remote-agent/PROTOCOL.md) has no separate peer ID at all (sessions are
addressed by the existing managed_hosts.id), and agent_key replaces
rustdesk_key as the one control-channel credential, server-minted instead of
agent-reported.

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-18

"""
import sqlalchemy as sa
from alembic import op

revision = "0045"
down_revision = "0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("managed_hosts", sa.Column("agent_key_encrypted", sa.LargeBinary(), nullable=True))
    op.drop_column("managed_hosts", "rustdesk_id")
    op.drop_column("managed_hosts", "rustdesk_key_encrypted")


def downgrade() -> None:
    op.add_column("managed_hosts", sa.Column("rustdesk_key_encrypted", sa.LargeBinary(), nullable=True))
    op.add_column("managed_hosts", sa.Column("rustdesk_id", sa.String(64), nullable=True))
    op.drop_column("managed_hosts", "agent_key_encrypted")
