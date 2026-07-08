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
  email: string;
  display_name: string;
  global_role: Role;
  is_active: boolean;
}

export type HypervisorType = "esxi" | "proxmox";
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
  default_network: string | null;
  last_test_status: ConnectionStatus;
  last_test_at: string | null;
  last_test_message: string | null;
}

export interface DiskLayoutJson {
  efi_size_mb: number;
  msr_size_mb: number;
  os_volume: "remaining" | { size_mb: number };
  extra_volumes: { label: string; drive_letter: string; size_mb: number }[];
}

export interface DiskLayout {
  id: string;
  org_id: string | null;
  name: string;
  layout_json: DiskLayoutJson;
}

export type IsoKind = "windows_iso" | "virtio_iso";
export type UploadStatus = "pending" | "uploading" | "complete" | "failed";

export interface IsoAsset {
  id: string;
  org_id: string | null;
  kind: IsoKind;
  filename: string;
  checksum_sha256: string | null;
  size_bytes: number;
  upload_status: UploadStatus;
}

export type DomainJoinTiming = "answer_file" | "post_install";

export interface PostInstallScript {
  name: string;
  script_text: string;
}

export interface DeploymentTemplate {
  id: string;
  org_id: string | null;
  name: string;
  iso_asset_id: string | null;
  disk_layout_id: string;
  cpu_count: number;
  ram_mb: number;
  disk_size_gb: number;
  network_name: string;
  vlan_id: number | null;
  locale: string;
  timezone: string;
  keyboard_layout: string;
  domain_join_enabled: boolean;
  domain_fqdn: string | null;
  domain_join_account: string | null;
  domain_target_ou: string | null;
  domain_join_timing: DomainJoinTiming;
  windows_features: string[];
  post_install_scripts: PostInstallScript[];
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
  template_id: string;
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
  created_by_user_id: string;
  created_at: string;
  updated_at: string;
}

export interface DeploymentStateTransition {
  from_state: string;
  to_state: string;
  occurred_at: string;
  detail: string | null;
}

export interface DeploymentLogLine {
  ts: string;
  stage: string;
  level: "info" | "warn" | "error";
  message: string;
}
