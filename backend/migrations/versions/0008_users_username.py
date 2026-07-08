"""users: username replaces email as identifier, email becomes optional

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(64), nullable=True))
    # backfill any existing rows (none expected on a fresh instance) from the email local part
    op.execute("UPDATE users SET username = split_part(email, '@', 1) WHERE username IS NULL")
    op.alter_column("users", "username", nullable=False)
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.drop_index("ix_users_email", table_name="users")
    op.alter_column("users", "email", nullable=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_email", table_name="users")
    op.alter_column("users", "email", nullable=False)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.drop_index("ix_users_username", table_name="users")
    op.drop_column("users", "username")
