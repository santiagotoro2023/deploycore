export type Role = "none" | "readonly" | "operator" | "admin";

export interface Organization {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  is_active: boolean;
}

export interface User {
  id: string;
  username: string;
  email: string | null;
  display_name: string;
  global_role: Role;
  is_active: boolean;
  totp_enabled: boolean;
  has_avatar: boolean;
  org_roles: Record<string, Role>;
}

export type HypervisorType = "esxi";
export type ConnectionStatus = "unknown" | "ok" | "failed";

export interface HypervisorHost {
  id: string;
  org_id: string;
  name: string;
  type: HypervisorType;
  api_endpoint: string;
  username: string;
  tls_verify: boolean;
  default_datastore: string | null;
  last_test_status: ConnectionStatus;
  last_test_at: string | null;
  last_test_message: string | null;
}

export interface DiskLayoutJson {
  efi_size_mb: number;
  msr_size_mb: number;
  recovery_size_mb: number | null;
  os_volume: "remaining" | { size_mb: number };
  extra_volumes: { label: string; drive_letter: string; size_mb: number }[];
}

export interface DiskLayout {
  id: string;
  org_id: string | null;
  name: string;
  layout_json: DiskLayoutJson;
  post_install_scripts: { name: string; script_text: string }[];
}

export type IsoKind = "windows_iso" | "virtio_iso";
export type UploadStatus = "pending" | "uploading" | "complete" | "failed";

export interface WindowsEditionInfo {
  index: number;
  name: string;
  description: string;
  has_gui: boolean;
}

export interface IsoAsset {
  id: string;
  org_id: string | null;
  kind: IsoKind;
  filename: string;
  checksum_sha256: string | null;
  size_bytes: number;
  upload_status: UploadStatus;
  windows_editions: WindowsEditionInfo[];
}

export type AppKind = "msi" | "exe";

export interface AppAsset {
  id: string;
  org_id: string | null;
  kind: AppKind;
  name: string;
  filename: string;
  checksum_sha256: string | null;
  size_bytes: number;
  default_install_args: string;
  upload_status: UploadStatus;
}

export interface AppInstallEntry {
  app_asset_id: string;
  install_args: string;
}

export type DomainJoinTiming = "answer_file" | "post_install";
export type DiskProvisioning = "thin" | "thick_lazy_zeroed" | "thick_eager_zeroed";
export type NetworkAdapterType = "vmxnet3" | "e1000" | "e1000e";

export interface PostInstallScript {
  name: string;
  script_text: string;
}

export interface DeploymentTemplate {
  id: string;
  org_id: string | null;
  name: string;
  iso_asset_id: string | null;
  image_index: number;
  disk_layout_id: string;
  cpu_count: number;
  cores_per_socket: number;
  ram_mb: number;
  disk_size_gb: number;
  disk_provisioning: DiskProvisioning;
  network_name: string;
  network_adapter_type: NetworkAdapterType;
  locale: string;
  timezone: string;
  keyboard_layout: string;
  custom_admin_enabled: boolean;
  local_admin_username: string;
  domain_join_enabled: boolean;
  domain_fqdn: string | null;
  domain_join_account: string | null;
  domain_target_ou: string | null;
  domain_join_timing: DomainJoinTiming;
  enable_rdp: boolean;
  windows_features: string[];
  post_install_scripts: PostInstallScript[];
  app_installs: AppInstallEntry[];
}

export type IpMode = "dhcp" | "static";
export type DeploymentState =
  | "pending"
  | "creating_vm"
  | "booting"
  | "installing_os"
  | "post_install"
  | "configuring"
  | "completed"
  | "failed";

export interface Deployment {
  id: string;
  org_id: string;
  template_id: string | null;
  hypervisor_host_id: string;
  hostname: string;
  ip_mode: IpMode;
  static_ip: string | null;
  static_netmask: string | null;
  static_gateway: string | null;
  static_dns: string[] | null;
  state: DeploymentState;
  vm_moref: string | null;
  error_message: string | null;
  retry_count: number;
  created_by_user_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface DeploymentStateTransition {
  from_state: string;
  to_state: string;
  occurred_at: string;
  detail: string | null;
}

export const WEBHOOK_EVENT_TYPES = [
  "deployment.start",
  "deployment.complete",
  "deployment.failed",
  "deployment.retry",
] as const;

export interface Webhook {
  id: string;
  org_id: string;
  name: string;
  url: string;
  enabled: boolean;
  events: string[];
}

export interface WebhookDelivery {
  event_type: string;
  status_code: number | null;
  success: boolean;
  response_snippet: string | null;
  occurred_at: string;
}

export interface DeploymentLogLine {
  ts: string;
  stage: string;
  level: "info" | "warn" | "error";
  message: string;
}
