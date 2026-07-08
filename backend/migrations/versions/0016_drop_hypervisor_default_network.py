"""hypervisor_hosts: drop unused default_network

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-09

"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("hypervisor_hosts", "default_network")


def downgrade() -> None:
    op.add_column("hypervisor_hosts", sa.Column("default_network", sa.String(255), nullable=True))
