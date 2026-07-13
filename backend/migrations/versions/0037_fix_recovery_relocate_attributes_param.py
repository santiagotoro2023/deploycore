"""disk_layouts: fix Set-Partition -Attributes, not a real parameter

Set-Partition has no -Attributes bitmask parameter at all - the real
cmdlet exposes the GPT hidden/no-default-drive-letter bits as separate
-IsHidden/-NoDefaultDriveLetter switches instead. Confirmed on a real
deployment that got all the way through the capture/apply/reagentc
sequence successfully and only failed on this last cosmetic step
("Es wurde kein Parameter gefunden, der dem Parameternamen 'Attributes'
entspricht"). A targeted string replace rather than swapping in the
whole script again (see 0036), since this is the only line that changed.

Revision ID: 0037
Revises: 0036
Create Date: 2026-07-13

"""
import json

from alembic import op
from sqlalchemy import text

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

SCRIPT_NAME = "Recovery partition relocation (disk layout from hell fix)"
_OLD = "-Attributes 0x8000000000000001"
_NEW = "-IsHidden $true -NoDefaultDriveLetter $true"


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, post_install_scripts FROM disk_layouts")).fetchall()
    for row_id, scripts in rows:
        if not scripts:
            continue
        changed = False
        for script in scripts:
            if script.get("name") == SCRIPT_NAME and _OLD in script.get("script_text", ""):
                script["script_text"] = script["script_text"].replace(_OLD, _NEW)
                changed = True
        if changed:
            conn.execute(
                text("UPDATE disk_layouts SET post_install_scripts = :scripts::jsonb WHERE id = :id"),
                {"scripts": json.dumps(scripts), "id": row_id},
            )


def downgrade() -> None:
    pass
