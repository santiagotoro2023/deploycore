"""deployments: add deleted_at for soft delete

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-11

"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("deployments", "deleted_at")
