"""disk_layouts.post_install_scripts

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-12

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "disk_layouts",
        sa.Column("post_install_scripts", postgresql.JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("disk_layouts", "post_install_scripts")
