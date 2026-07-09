"""deployments: store the actual rendered autounattend.xml

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-13

"""
import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("rendered_autounattend", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("deployments", "rendered_autounattend")
