import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.db import get_db
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


def _test_database_url() -> str:
    """A dedicated `<dbname>_test` database, never the app's own
    DATABASE_URL: this fixture module create/drops the whole schema per
    test session, and running that against the same database the live app
    (or an operator's `make dev`) is using would destroy real data."""
    root, _, name = get_settings().database_url.rpartition("/")
    return f"{root}/{name}_test"


TEST_DATABASE_URL = _test_database_url()
engine = create_async_engine(TEST_DATABASE_URL)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _schema():
    admin_url = TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
    db_name = TEST_DATABASE_URL.rsplit("/", 1)[-1]
    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name})
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    await admin_engine.dispose()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


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
        username=f"user-{uuid.uuid4().hex[:8]}",
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


async def auth_headers(user: User) -> dict[str, str]:
    from redis.asyncio import Redis

    from app.config import get_settings
    from app.security.auth import create_access_token
    from app.security.sessions import create_session

    # A fresh connection per call rather than the app's process-wide
    # get_redis() lru_cache: pytest-asyncio gives each test its own event
    # loop, and a cached async client from a prior test's (closed) loop
    # raises "Event loop is closed" here.
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        session_id = await create_session(redis, user.id)
    finally:
        await redis.aclose()
    return {"Authorization": f"Bearer {create_access_token(user.id, session_id)}"}
