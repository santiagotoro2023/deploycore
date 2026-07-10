"""deployments.template_id: nullable, ON DELETE SET NULL

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-10

"""
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_CONSTRAINT = "deployments_template_id_fkey"


def upgrade() -> None:
    op.alter_column("deployments", "template_id", nullable=True)
    op.drop_constraint(_CONSTRAINT, "deployments", type_="foreignkey")
    op.create_foreign_key(
        _CONSTRAINT, "deployments", "deployment_templates", ["template_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "deployments", type_="foreignkey")
    op.create_foreign_key(_CONSTRAINT, "deployments", "deployment_templates", ["template_id"], ["id"])
    op.alter_column("deployments", "template_id", nullable=False)
