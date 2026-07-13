"""remove the automatic post-deployment health-check feature entirely

Explicitly requested: the reachability check depended on
HypervisorDriver.get_guest_ip(), which for a non-static (DHCP)
deployment requires VMware Tools to be installed in the guest - if
Tools was never installed (e.g. a non-ESXi host, or the Tools ISO
wasn't mounted), the check could never determine the guest's IP at all
and permanently reported "unreachable" regardless of the VM's actual
state, confirmed on a real deployment that was reachable the whole
time. Rather than special-case that gap, the whole feature is being
dropped - the operator already has their own external monitoring.

Drops: deployments.last_health_status/last_health_checked_at, the
deployment_health_checks table, the health_status enum type, the
notification_preferences email/teams_on_health_degraded columns, and
the seeded "health_degraded" notification_templates row.

Revision ID: 0039
Revises: 0038
Create Date: 2026-07-13

"""
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision = "0039"
down_revision = "0038"
branch_labels = None
depends_on = None

health_status_enum = postgresql.ENUM("unknown", "healthy", "unreachable", name="health_status", create_type=False)


def upgrade() -> None:
    op.execute(text("DELETE FROM notification_templates WHERE event_type = 'health_degraded'"))
    op.drop_column("notification_preferences", "email_on_health_degraded")
    op.drop_column("notification_preferences", "teams_on_health_degraded")

    op.drop_index("ix_deployment_health_checks_deployment_id_checked_at", table_name="deployment_health_checks")
    op.drop_table("deployment_health_checks")

    op.drop_column("deployments", "last_health_checked_at")
    op.drop_column("deployments", "last_health_status")
    health_status_enum.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    pass
