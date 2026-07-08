"""users: avatar_filename

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("avatar_filename", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_filename")
