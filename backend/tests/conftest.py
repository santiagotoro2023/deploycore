import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import engine, get_db
from app.main import app
from app.models import (
    Base,
    Deployment,
    DeploymentTemplate,
    DiskLayout,
    HypervisorHost,
    HypervisorType,
    IpMode,
    IsoAsset,
    IsoKind,
    Organization,
    Role,
    User,
    UserOrgRole,
)
from app.security.auth import hash_password


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    # ponytail: runs against DATABASE_URL directly rather than standing up a
    # separate test-database fixture; fine at this scale, split out a
    # TEST_DATABASE_URL if the dev DB ever needs to stay untouched by tests.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session():
    async with engine.connect() as conn:
        await conn.begin()
        session_factory = async_sessionmaker(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        async with session_factory() as session:
            yield session
        await conn.rollback()


@pytest_asyncio.fixture
async def test_client(db_session):
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def make_organization(db_session, **kwargs) -> Organization:
    org = Organization(
        name=kwargs.get("name", f"Org {uuid.uuid4().hex[:8]}"),
        slug=kwargs.get("slug", f"org-{uuid.uuid4().hex[:8]}"),
        description=kwargs.get("description"),
    )
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


async def make_user(db_session, *, global_role: Role = Role.NONE, org: Organization | None = None, org_role: Role | None = None) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("Passw0rd!"),
        display_name="Test User",
        global_role=global_role,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    if org is not None and org_role is not None:
        db_session.add(UserOrgRole(user_id=user.id, org_id=org.id, role=org_role))
        await db_session.commit()
    return user


async def make_disk_layout(db_session, org: Organization | None = None) -> DiskLayout:
    layout = DiskLayout(
        org_id=org.id if org else None,
        name=f"layout-{uuid.uuid4().hex[:8]}",
        layout_json={"efi_size_mb": 500, "msr_size_mb": 128, "os_volume": "remaining", "extra_volumes": []},
    )
    db_session.add(layout)
    await db_session.commit()
    await db_session.refresh(layout)
    return layout


async def make_iso_asset(db_session, org: Organization | None = None, kind: IsoKind = IsoKind.WINDOWS_ISO) -> IsoAsset:
    iso = IsoAsset(
        org_id=org.id if org else None,
        kind=kind,
        filename="test.iso",
        storage_path="/data/isos/test.iso",
        checksum_sha256="0" * 64,
        size_bytes=1024,
    )
    db_session.add(iso)
    await db_session.commit()
    await db_session.refresh(iso)
    return iso


async def make_hypervisor_host(db_session, org: Organization) -> HypervisorHost:
    host = HypervisorHost(
        org_id=org.id,
        name=f"host-{uuid.uuid4().hex[:8]}",
        type=HypervisorType.ESXI,
        api_endpoint="esxi.test.local",
        username="root",
        default_datastore="datastore1",
        default_network="VM Network",
    )
    host.credential = "hunter2"
    db_session.add(host)
    await db_session.commit()
    await db_session.refresh(host)
    return host


async def make_template(db_session, org: Organization, disk_layout: DiskLayout, iso_asset: IsoAsset) -> DeploymentTemplate:
    template = DeploymentTemplate(
        org_id=org.id,
        name=f"template-{uuid.uuid4().hex[:8]}",
        iso_asset_id=iso_asset.id,
        disk_layout_id=disk_layout.id,
        cpu_count=2,
        ram_mb=4096,
        disk_size_gb=80,
        network_name="VM Network",
        windows_features=[],
        post_install_scripts=[],
    )
    template.local_admin_password = "P@ssw0rd1!"
    db_session.add(template)
    await db_session.commit()
    await db_session.refresh(template)
    return template


async def make_deployment(
    db_session, org: Organization, template: DeploymentTemplate, host: HypervisorHost, user: User
) -> Deployment:
    deployment = Deployment(
        org_id=org.id,
        template_id=template.id,
        hypervisor_host_id=host.id,
        hostname=f"host-{uuid.uuid4().hex[:8]}",
        ip_mode=IpMode.DHCP,
        callback_token=uuid.uuid4().hex,
        created_by_user_id=user.id,
    )
    db_session.add(deployment)
    await db_session.commit()
    await db_session.refresh(deployment)
    return deployment


def auth_headers(user: User) -> dict[str, str]:
    from app.security.auth import create_access_token

    return {"Authorization": f"Bearer {create_access_token(user.id)}"}
