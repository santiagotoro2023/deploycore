import enum
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin, enum_column
from app.security import crypto


class DomainJoinTiming(str, enum.Enum):
    ANSWER_FILE = "answer_file"
    POST_INSTALL = "post_install"


class DiskProvisioning(str, enum.Enum):
    THIN = "thin"
    THICK_LAZY_ZEROED = "thick_lazy_zeroed"
    THICK_EAGER_ZEROED = "thick_eager_zeroed"


class NetworkAdapterType(str, enum.Enum):
    VMXNET3 = "vmxnet3"
    E1000 = "e1000"
    E1000E = "e1000e"


class DeploymentTemplate(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "deployment_templates"

    org_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # nullable: a template can exist (e.g. seeded demo data) before an
    # operator has uploaded the Windows ISO it deploys from, the
    # provisioning pipeline itself refuses to run without one.
    iso_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("iso_assets.id", ondelete="SET NULL"), nullable=True
    )
    # Which edition inside the ISO's install.wim to install (Windows
    # Server media typically bundles several: Standard/Datacenter x Server
    # Core/Desktop Experience). Default 1 is not a considered choice, it's
    # what /IMAGE/INDEX always hardcoded before this field existed, on
    # Microsoft's usual ordering that's Server Core; the ISO asset's own
    # windows_editions (detected at upload time) is what the UI offers as
    # actual named choices instead of a bare number.
    image_index: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    disk_layout_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("disk_layouts.id"), nullable=False
    )

    cpu_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cores_per_socket: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_size_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_provisioning: Mapped[DiskProvisioning] = enum_column(
        DiskProvisioning, "disk_provisioning", default=DiskProvisioning.THIN, nullable=False
    )
    network_name: Mapped[str] = mapped_column(String(255), nullable=False)
    network_adapter_type: Mapped[NetworkAdapterType] = enum_column(
        NetworkAdapterType, "network_adapter_type", default=NetworkAdapterType.VMXNET3, nullable=False
    )
    # Just a name, not a foreign key to a specific HypervisorHost - a
    # template isn't bound to one host any more than network_name's port
    # group name is, both are conventionally similar across the hosts a
    # given template might actually deploy onto. None (the default)
    # means "use whichever host ends up selected at deploy time's own
    # default_datastore" - see HypervisorDriver.list_datastores(), what
    # the template/customize-installation form's own dropdown is
    # populated from.
    preferred_datastore: Mapped[str | None] = mapped_column(String(255), nullable=True)

    locale: Mapped[str] = mapped_column(String(20), default="en-US", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    keyboard_layout: Mapped[str] = mapped_column(String(20), default="en-US", nullable=False)

    # Off by default: the built-in Administrator account is used as-is. On:
    # a new local account (local_admin_username) is created and added to
    # Administrators, and the built-in Administrator is disabled during
    # Setup, see autounattend_base.xml.j2's LocalAccounts block and
    # _first_logon_commands.xml.j2. The route layer (templates.py) is what
    # keeps local_admin_username forced to "Administrator" whenever this is
    # False, so every WinRM call downstream can just always use
    # local_admin_username without needing to know about this flag itself.
    custom_admin_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    local_admin_username: Mapped[str] = mapped_column(String(64), default="Administrator", nullable=False)
    local_admin_password_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    domain_join_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    domain_fqdn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_join_account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_join_credential_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    domain_target_ou: Mapped[str | None] = mapped_column(String(512), nullable=True)
    domain_join_timing: Mapped[DomainJoinTiming] = enum_column(
        DomainJoinTiming, "domain_join_timing", default=DomainJoinTiming.ANSWER_FILE, nullable=False
    )

    # On by default: most deployments are managed interactively at some
    # point (even ones primarily driven over WinRM), and WinRM itself is
    # deliberately closed for good once post_install finishes (see
    # provision.py's run_post_install), so RDP left off by default would
    # leave a freshly-deployed VM with no remote access at all once that
    # happens.
    enable_rdp: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # On by default (matches previous unconditional behavior). Off is a
    # deliberate speed/currency trade-off for deployments that need to be
    # quick - see install_windows_updates' own docstring for the size-skip
    # trade-off already baked into what "on" means.
    install_windows_updates: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    windows_features: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    post_install_scripts: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    # Ordered list of {"app_asset_id": "<uuid str>", "install_args": "<str>"},
    # installed over WinRM in list order, after Windows features and before
    # post_install_scripts (so a script can assume an app is already
    # installed). install_args overrides the AppAsset's own
    # default_install_args for this one attachment; an empty string falls
    # back to the asset's default at install time.
    app_installs: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    @property
    def local_admin_password(self) -> str:
        return crypto.decrypt(self.local_admin_password_encrypted)

    @local_admin_password.setter
    def local_admin_password(self, value: str) -> None:
        self.local_admin_password_encrypted = crypto.encrypt(value)

    @property
    def domain_join_credential(self) -> str | None:
        if self.domain_join_credential_encrypted is None:
            return None
        return crypto.decrypt(self.domain_join_credential_encrypted)

    @domain_join_credential.setter
    def domain_join_credential(self, value: str) -> None:
        self.domain_join_credential_encrypted = crypto.encrypt(value)
