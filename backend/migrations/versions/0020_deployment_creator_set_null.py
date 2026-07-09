"""deployments.created_by_user_id: nullable, ON DELETE SET NULL

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-13

"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_CONSTRAINT = "deployments_created_by_user_id_fkey"


def upgrade() -> None:
    op.alter_column("deployments", "created_by_user_id", nullable=True)
    op.drop_constraint(_CONSTRAINT, "deployments", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT, "deployments", "users", ["created_by_user_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "deployments", type_="foreignkey")
    op.create_foreign_key(_CONSTRAINT, "deployments", "users", ["created_by_user_id"], ["id"])
    op.alter_column("deployments", "created_by_user_id", nullable=False)
