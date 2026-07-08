import uuid

from pydantic import BaseModel, ConfigDict

from app.models.template import DomainJoinTiming


class PostInstallScript(BaseModel):
    name: str
    script_text: str


class DeploymentTemplateCreate(BaseModel):
    name: str
    iso_asset_id: uuid.UUID | None = None
    disk_layout_id: uuid.UUID
    cpu_count: int
    ram_mb: int
    disk_size_gb: int
    network_name: str
    vlan_id: int | None = None
    locale: str = "en-US"
    timezone: str = "UTC"
    keyboard_layout: str = "en-US"
    local_admin_password: str
    domain_join_enabled: bool = False
    domain_fqdn: str | None = None
    domain_join_account: str | None = None
    domain_join_credential: str | None = None
    domain_target_ou: str | None = None
    domain_join_timing: DomainJoinTiming = DomainJoinTiming.ANSWER_FILE
    windows_features: list[str] = []
    post_install_scripts: list[PostInstallScript] = []


class DeploymentTemplateUpdate(BaseModel):
    name: str | None = None
    iso_asset_id: uuid.UUID | None = None
    disk_layout_id: uuid.UUID | None = None
    cpu_count: int | None = None
    ram_mb: int | None = None
    disk_size_gb: int | None = None
    network_name: str | None = None
    vlan_id: int | None = None
    locale: str | None = None
    timezone: str | None = None
    keyboard_layout: str | None = None
    local_admin_password: str | None = None
    domain_join_enabled: bool | None = None
    domain_fqdn: str | None = None
    domain_join_account: str | None = None
    domain_join_credential: str | None = None
    domain_target_ou: str | None = None
    domain_join_timing: DomainJoinTiming | None = None
    windows_features: list[str] | None = None
    post_install_scripts: list[PostInstallScript] | None = None


class DeploymentTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID | None
    name: str
    iso_asset_id: uuid.UUID | None
    disk_layout_id: uuid.UUID
    cpu_count: int
    ram_mb: int
    disk_size_gb: int
    network_name: str
    vlan_id: int | None
    locale: str
    timezone: str
    keyboard_layout: str
    domain_join_enabled: bool
    domain_fqdn: str | None
    domain_join_account: str | None
    domain_target_ou: str | None
    domain_join_timing: DomainJoinTiming
    windows_features: list[str]
    post_install_scripts: list[PostInstallScript]
