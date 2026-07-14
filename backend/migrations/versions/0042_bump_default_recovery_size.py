"""disk_layouts: bump the seeded default recovery_size_mb from 1000 to 1024

Explicitly requested: 1000 MB was never a technical requirement, just
this project's own chosen default (well above the 300 MB floor) - 1024
(an exact 1 GiB) is what was actually wanted. Only touches rows still at
the exact old default value, not any layout an operator deliberately set
to something else.

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-16

"""
import json

from alembic import op
from sqlalchemy import text

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None

OLD_DEFAULT = 1000
NEW_DEFAULT = 1024


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, layout_json FROM disk_layouts")).fetchall()
    for row_id, layout_json in rows:
        if layout_json.get("recovery_size_mb") == OLD_DEFAULT:
            layout_json["recovery_size_mb"] = NEW_DEFAULT
            conn.execute(
                text("UPDATE disk_layouts SET layout_json = :layout_json::jsonb WHERE id = :id"),
                {"layout_json": json.dumps(layout_json), "id": row_id},
            )


def downgrade() -> None:
    pass
