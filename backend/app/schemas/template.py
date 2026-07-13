import uuid

from pydantic import BaseModel, ConfigDict

from app.models.template import DiskProvisioning, DomainJoinTiming, NetworkAdapterType
from app.schemas.disk_layout import DiskLayoutJson, PostInstallScript

# Windows reserves these local account names (the built-in Administrator
# and Guest accounts, plus the two accounts newer Windows 10/11 SKUs also
# ship); DeployCore actively disables the built-in Administrator when the
# custom-admin toggle is on, so a custom account named "Administrator"
# would collide with the very account being disabled. Enforced in
# api/routes/templates.py, not here: whether it applies at all depends on
# custom_admin_enabled, a sibling field a single-field validator can't see.
RESERVED_LOCAL_ACCOUNT_NAMES = {"administrator", "guest", "defaultaccount", "wdagutilityaccount"}


class AppInstallEntry(BaseModel):
    app_asset_id: uuid.UUID
    # Overrides the AppAsset's own default_install_args for this one
    # attachment; blank means "use the asset's default" (see
    # worker/tasks/provision.py), not "no arguments at all".
    install_args: str = ""


class DeploymentTemplateCreate(BaseModel):
    name: str
    iso_asset_id: uuid.UUID | None = None
    # Which edition inside the ISO's install.wim to install; 1 matches
    # what was hardcoded before this field existed (typically Server Core
    # on Microsoft's usual multi-edition ordering), not a considered
    # default. The ISO asset's own detected windows_editions is what the
    # UI shows as actual named choices.
    image_index: int = 1
    disk_layout_id: uuid.UUID
    cpu_count: int
    cores_per_socket: int = 1
    ram_mb: int
    disk_size_gb: int
    disk_provisioning: DiskProvisioning = DiskProvisioning.THIN
    network_name: str
    network_adapter_type: NetworkAdapterType = NetworkAdapterType.VMXNET3
    preferred_datastore: str | None = None
    locale: str = "de-DE"
    timezone: str = "W. Europe Standard Time"
    keyboard_layout: str = "de-CH"
    custom_admin_enabled: bool = False
    local_admin_username: str = "svcadmin"
    local_admin_password: str
    domain_join_enabled: bool = False
    domain_fqdn: str | None = None
    domain_join_account: str | None = None
    domain_join_credential: str | None = None
    domain_target_ou: str | None = None
    domain_join_timing: DomainJoinTiming = DomainJoinTiming.ANSWER_FILE
    enable_rdp: bool = True
    install_windows_updates: bool = True
    windows_features: list[str] = []
    post_install_scripts: list[PostInstallScript] = []
    app_installs: list[AppInstallEntry] = []


class DeploymentTemplateUpdate(BaseModel):
    name: str | None = None
    iso_asset_id: uuid.UUID | None = None
    image_index: int | None = None
    disk_layout_id: uuid.UUID | None = None
    cpu_count: int | None = None
    cores_per_socket: int | None = None
    ram_mb: int | None = None
    disk_size_gb: int | None = None
    disk_provisioning: DiskProvisioning | None = None
    network_name: str | None = None
    network_adapter_type: NetworkAdapterType | None = None
    preferred_datastore: str | None = None
    locale: str | None = None
    timezone: str | None = None
    keyboard_layout: str | None = None
    custom_admin_enabled: bool | None = None
    local_admin_username: str | None = None
    local_admin_password: str | None = None
    domain_join_enabled: bool | None = None
    domain_fqdn: str | None = None
    domain_join_account: str | None = None
    domain_join_credential: str | None = None
    domain_target_ou: str | None = None
    domain_join_timing: DomainJoinTiming | None = None
    enable_rdp: bool | None = None
    install_windows_updates: bool | None = None
    windows_features: list[str] | None = None
    post_install_scripts: list[PostInstallScript] | None = None
    app_installs: list[AppInstallEntry] | None = None


class DeploymentTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    name: str
    iso_asset_id: uuid.UUID | None
    image_index: int
    disk_layout_id: uuid.UUID
    cpu_count: int
    cores_per_socket: int
    ram_mb: int
    disk_size_gb: int
    disk_provisioning: DiskProvisioning
    network_name: str
    network_adapter_type: NetworkAdapterType
    preferred_datastore: str | None
    locale: str
    timezone: str
    keyboard_layout: str
    custom_admin_enabled: bool
    local_admin_username: str
    domain_join_enabled: bool
    domain_fqdn: str | None
    domain_join_account: str | None
    domain_target_ou: str | None
    domain_join_timing: DomainJoinTiming
    enable_rdp: bool
    install_windows_updates: bool
    windows_features: list[str]
    post_install_scripts: list[PostInstallScript]
    app_installs: list[AppInstallEntry]


class TemplateExportDiskLayout(BaseModel):
    name: str
    layout: DiskLayoutJson


class TemplateExportIsoHint(BaseModel):
    filename: str
    kind: str


class DeploymentTemplateExport(BaseModel):
    """Credentials are deliberately omitted, an import always starts with
    a random placeholder password that must be replaced before the
    template is deployable. iso_hint and app_install_hints are
    informational only: an ISO or an app asset isn't portable across
    environments (a matching id may not exist, or may exist and mean
    something else), so import leaves iso_asset_id unset and
    app_installs empty, the same reasoning for both. preferred_datastore
    is dropped outright for the same reason, one environment's datastore
    name means nothing (or worse, means something else) in another."""

    name: str
    disk_layout: TemplateExportDiskLayout
    iso_hint: TemplateExportIsoHint | None
    app_install_hints: list[str]
    cpu_count: int
    cores_per_socket: int
    ram_mb: int
    disk_size_gb: int
    disk_provisioning: DiskProvisioning
    network_name: str
    network_adapter_type: NetworkAdapterType
    locale: str
    timezone: str
    keyboard_layout: str
    custom_admin_enabled: bool
    local_admin_username: str
    domain_join_enabled: bool
    domain_fqdn: str | None
    domain_join_account: str | None
    domain_target_ou: str | None
    domain_join_timing: DomainJoinTiming
    enable_rdp: bool
    install_windows_updates: bool
    windows_features: list[str]
    post_install_scripts: list[PostInstallScript]


class DeploymentTemplateImport(BaseModel):
    name: str
    disk_layout: TemplateExportDiskLayout
    cpu_count: int
    cores_per_socket: int = 1
    ram_mb: int
    disk_size_gb: int
    disk_provisioning: DiskProvisioning = DiskProvisioning.THIN
    network_name: str
    network_adapter_type: NetworkAdapterType = NetworkAdapterType.VMXNET3
    locale: str = "de-DE"
    timezone: str = "W. Europe Standard Time"
    keyboard_layout: str = "de-CH"
    custom_admin_enabled: bool = False
    local_admin_username: str = "svcadmin"
    domain_join_enabled: bool = False
    domain_fqdn: str | None = None
    domain_join_account: str | None = None
    domain_target_ou: str | None = None
    domain_join_timing: DomainJoinTiming = DomainJoinTiming.ANSWER_FILE
    enable_rdp: bool = True
    install_windows_updates: bool = True
    windows_features: list[str] = []
    post_install_scripts: list[PostInstallScript] = []
