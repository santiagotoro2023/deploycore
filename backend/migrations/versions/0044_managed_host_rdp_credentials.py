"""managed_hosts RDP credentials (rdp_username, rdp_password_encrypted)

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-16

"""
import sqlalchemy as sa
from alembic import op

revision = "0044"
down_revision = "0043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("managed_hosts", sa.Column("rdp_username", sa.String(255), nullable=True))
    op.add_column("managed_hosts", sa.Column("rdp_password_encrypted", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    op.drop_column("managed_hosts", "rdp_password_encrypted")
    op.drop_column("managed_hosts", "rdp_username")
