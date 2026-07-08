"""Seeds a demo organization, users, disk layout, and template.

Run with: python scripts/seed.py (inside the api container / backend venv).
"""

import asyncio
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models.disk_layout import DiskLayout
from app.models.org import Organization
from app.models.template import DeploymentTemplate
from app.models.user import Role, User, UserOrgRole
from app.security.auth import hash_password

DEMO_PASSWORD = "ChangeMe123!"

DEMO_USERS = [
    ("admin@example.com", "Demo Admin", Role.ADMIN),
    ("operator@example.com", "Demo Operator", Role.OPERATOR),
    ("readonly@example.com", "Demo Readonly", Role.READONLY),
]


async def seed() -> None:
    async with SessionLocal() as db:
        existing = await db.execute(select(Organization).where(Organization.slug == "acme-demo"))
        if existing.scalar_one_or_none() is not None:
            print("Seed data already present (organization 'acme-demo' exists) — nothing to do.")
            return

        org = Organization(name="Acme MSP Demo", slug="acme-demo", description="Demo organization from scripts/seed.py")
        db.add(org)
        await db.flush()

        for email, display_name, role in DEMO_USERS:
            user = User(email=email, password_hash=hash_password(DEMO_PASSWORD), display_name=display_name)
            db.add(user)
            await db.flush()
            db.add(UserOrgRole(user_id=user.id, org_id=org.id, role=role))

        disk_layout = DiskLayout(
            org_id=org.id,
            name="Standard 100GB",
            layout_json={"efi_size_mb": 500, "msr_size_mb": 128, "os_volume": "remaining", "extra_volumes": []},
        )
        db.add(disk_layout)
        await db.flush()

        template = DeploymentTemplate(
            org_id=org.id,
            name="Windows Server 2025 - Standard",
            iso_asset_id=None,
            disk_layout_id=disk_layout.id,
            cpu_count=4,
            ram_mb=8192,
            disk_size_gb=100,
            network_name="VM Network",
            domain_join_enabled=False,
            windows_features=["Web-Server"],
            post_install_scripts=[],
        )
        template.local_admin_password = "ChangeMe123!"
        db.add(template)

        await db.commit()

    print("Seed complete.")
    print("  Organization: Acme MSP Demo (acme-demo)")
    print(f"  Users (password: {DEMO_PASSWORD}):")
    for email, _, role in DEMO_USERS:
        print(f"    {email}  [{role.value}]")
    print("  Disk layout: Standard 100GB")
    print("  Template: Windows Server 2025 - Standard (workgroup, IIS role)")
    print()
    print("Next steps (not automated — these need real binaries/credentials):")
    print("  1. Upload a Windows Server 2025 ISO via ISO Assets, then attach it to the template.")
    print("  2. Register a hypervisor host under Hypervisors and run Test Connection.")


if __name__ == "__main__":
    try:
        asyncio.run(seed())
    except Exception as exc:  # noqa: BLE001 - top-level script entry point
        print(f"Seed failed: {exc}", file=sys.stderr)
        sys.exit(1)
