"""iso_assets

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-08

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

iso_kind_enum = postgresql.ENUM("windows_iso", "virtio_iso", name="iso_kind", create_type=False)
upload_status_enum = postgresql.ENUM(
    "pending", "uploading", "complete", "failed", name="upload_status", create_type=False
)


def upgrade() -> None:
    iso_kind_enum.create(op.get_bind(), checkfirst=True)
    upload_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "iso_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "org_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("kind", iso_kind_enum, nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_path", sa.String(1024), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "upload_status",
            upload_status_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_iso_assets_org_id", "iso_assets", ["org_id"])


def downgrade() -> None:
    op.drop_index("ix_iso_assets_org_id", table_name="iso_assets")
    op.drop_table("iso_assets")
    upload_status_enum.drop(op.get_bind(), checkfirst=True)
    iso_kind_enum.drop(op.get_bind(), checkfirst=True)
