import { Copy, Download, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import ConfirmDialog from "../components/ConfirmDialog";
import DataTable from "../components/DataTable";
import Select from "../components/Select";
import { downloadJson, readJsonFile } from "../lib/jsonFile";
import { AppAsset, AppInstallEntry, DeploymentTemplate, DiskLayout, DiskProvisioning, IsoAsset, NetworkAdapterType } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

const WINDOWS_FEATURES: { name: string; label: string }[] = [
  { name: "AD-Domain-Services", label: "Active Directory Domain Services" },
  { name: "DNS", label: "DNS Server" },
  { name: "DHCP", label: "DHCP Server" },
  { name: "Web-Server", label: "Web Server (IIS)" },
  { name: "Print-Services", label: "Print Services" },
  { name: "RDS-RD-Server", label: "RD Session Host" },
  { name: "FS-DFS-Namespace", label: "DFS Namespaces" },
  { name: "FS-DFS-Replication", label: "DFS Replication" },
];

export default function Templates() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [templates, setTemplates] = useState<DeploymentTemplate[]>([]);
  const [diskLayouts, setDiskLayouts] = useState<DiskLayout[]>([]);
  const [isoAssets, setIsoAssets] = useState<IsoAsset[]>([]);
  const [appAssets, setAppAssets] = useState<AppAsset[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<DeploymentTemplate | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<DeploymentTemplate | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);

  async function load() {
    if (!selectedOrgId) return;
    const [t, d, i, a] = await Promise.all([
      api.get<DeploymentTemplate[]>(`/organizations/${selectedOrgId}/templates`),
      api.get<DiskLayout[]>(`/organizations/${selectedOrgId}/disk-layouts`),
      api.get<IsoAsset[]>(`/organizations/${selectedOrgId}/iso-assets`),
      api.get<AppAsset[]>(`/organizations/${selectedOrgId}/app-assets`),
    ]);
    setTemplates(t);
    setDiskLayouts(d);
    setIsoAssets(i.filter((iso) => iso.kind === "windows_iso"));
    setAppAssets(a);
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;
  const canManage = roleAtLeast(effectiveRole(selectedOrgId), "operator");

  async function cloneTemplate(templateId: string) {
    await api.post(`/organizations/${selectedOrgId}/templates/${templateId}/clone`);
    await load();
  }

  async function exportTemplate(t: DeploymentTemplate) {
    const data = await api.get(`/organizations/${selectedOrgId}/templates/${t.id}/export`);
    downloadJson(`template-${t.name.toLowerCase().replace(/\s+/g, "-")}.json`, data);
  }

  async function importTemplate(file: File | undefined) {
    if (!file || !selectedOrgId) return;
    setImportError(null);
    try {
      const data = await readJsonFile(file);
      await api.post(`/organizations/${selectedOrgId}/templates/import`, data);
      await load();
    } catch (err) {
      setImportError(err instanceof ApiError ? err.message : "Import failed: invalid or incompatible file.");
    }
  }

  async function deleteTemplate() {
    if (!confirmDelete) return;
    setDeleteError(null);
    try {
      await api.delete(`/organizations/${selectedOrgId}/templates/${confirmDelete.id}`);
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "Failed to delete this template.");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Templates</h1>
        {canManage && (
          <div className="flex items-center gap-2">
            <button
              className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
              onClick={() => importInputRef.current?.click()}
            >
              <Upload size={15} strokeWidth={1.75} />
              Import
            </button>
            <input
              ref={importInputRef}
              type="file"
              accept=".json"
              className="hidden"
              onChange={(e) => importTemplate(e.target.files?.[0])}
            />
            <button
              className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
              onClick={() => setShowCreate(true)}
            >
              <Plus size={15} strokeWidth={2} />
              New template
            </button>
          </div>
        )}
      </div>
      {importError && <div className="text-xs text-red-600">{importError}</div>}

      <DataTable<DeploymentTemplate>
        rows={templates}
        rowKey={(t) => t.id}
        searchValue={(t) => t.name}
        columns={[
          { key: "name", header: "Name", render: (t) => t.name, sortValue: (t) => t.name },
          { key: "scope", header: "Scope", render: (t) => (t.org_id ? "Organization" : "Global") },
          {
            key: "iso",
            header: "Windows ISO",
            render: (t) => (t.iso_asset_id ? <Badge value="ok" /> : <Badge value="failed" />),
          },
          {
            key: "sizing",
            header: "CPU / RAM / Disk",
            render: (t) => `${t.cpu_count} vCPU (${t.cores_per_socket}/socket) / ${t.ram_mb} MB / ${t.disk_size_gb} GB`,
          },
          { key: "domain", header: "Domain join", render: (t) => (t.domain_join_enabled ? t.domain_fqdn : "Workgroup") },
          { key: "features", header: "Roles", render: (t) => t.windows_features.join(", ") || "(none)" },
          {
            key: "actions",
            header: "",
            render: (t) =>
              canManage && (
                <div className="flex items-center gap-1.5">
                  {t.org_id === selectedOrgId && (
                    <button
                      className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                      onClick={() => setEditing(t)}
                    >
                      <Pencil size={12} strokeWidth={1.75} />
                      Edit
                    </button>
                  )}
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                    onClick={() => cloneTemplate(t.id)}
                  >
                    <Copy size={12} strokeWidth={1.75} />
                    Clone
                  </button>
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                    onClick={() => exportTemplate(t)}
                  >
                    <Download size={12} strokeWidth={1.75} />
                    Export
                  </button>
                  {t.org_id === selectedOrgId && (
                    <button
                      className="flex items-center gap-1 rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                      onClick={() => {
                        setDeleteError(null);
                        setConfirmDelete(t);
                      }}
                    >
                      <Trash2 size={12} strokeWidth={1.75} />
                    </button>
                  )}
                </div>
              ),
          },
        ]}
      />

      {showCreate && (
        <TemplateForm
          orgId={selectedOrgId}
          diskLayouts={diskLayouts}
          isoAssets={isoAssets}
          appAssets={appAssets}
          onClose={() => setShowCreate(false)}
          onSaved={async () => {
            setShowCreate(false);
            await load();
          }}
        />
      )}
      {editing && (
        <TemplateForm
          orgId={selectedOrgId}
          diskLayouts={diskLayouts}
          isoAssets={isoAssets}
          appAssets={appAssets}
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await load();
          }}
        />
      )}

      <ConfirmDialog
        open={!!confirmDelete}
        title="Delete template"
        message={
          <>
            {`Delete "${confirmDelete?.name}"? Deployments already created from it keep their own copy of these settings and are unaffected. This cannot be undone.`}
            {deleteError && <div className="mt-2 text-red-600 dark:text-red-400">{deleteError}</div>}
          </>
        }
        confirmLabel="Delete"
        onConfirm={deleteTemplate}
        onCancel={() => {
          setDeleteError(null);
          setConfirmDelete(null);
        }}
      />
    </div>
  );
}

function TemplateForm({
  orgId,
  diskLayouts,
  isoAssets,
  appAssets,
  existing,
  onClose,
  onSaved,
}: {
  orgId: string;
  diskLayouts: DiskLayout[];
  isoAssets: IsoAsset[];
  appAssets: AppAsset[];
  existing?: DeploymentTemplate;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!existing;
  const [name, setName] = useState(existing?.name ?? "");
  const [isoAssetId, setIsoAssetId] = useState(existing?.iso_asset_id ?? isoAssets[0]?.id ?? "");
  const [imageIndex, setImageIndex] = useState(existing?.image_index ?? 1);
  const [diskLayoutId, setDiskLayoutId] = useState(existing?.disk_layout_id ?? diskLayouts[0]?.id ?? "");
  const [cpuCount, setCpuCount] = useState(existing?.cpu_count ?? 2);
  const [coresPerSocket, setCoresPerSocket] = useState(existing?.cores_per_socket ?? 1);
  const [ramMb, setRamMb] = useState(existing?.ram_mb ?? 4096);
  const [diskSizeGb, setDiskSizeGb] = useState(existing?.disk_size_gb ?? 80);
  const [diskProvisioning, setDiskProvisioning] = useState<DiskProvisioning>(existing?.disk_provisioning ?? "thin");
  const [networkName, setNetworkName] = useState(existing?.network_name ?? "");
  const [networkAdapterType, setNetworkAdapterType] = useState<NetworkAdapterType>(existing?.network_adapter_type ?? "vmxnet3");
  const [locale, setLocale] = useState(existing?.locale ?? "de-DE");
  const [timezone, setTimezone] = useState(existing?.timezone ?? "W. Europe Standard Time");
  const [keyboardLayout, setKeyboardLayout] = useState(existing?.keyboard_layout ?? "de-CH");
  const [customAdminEnabled, setCustomAdminEnabled] = useState(existing?.custom_admin_enabled ?? false);
  // When custom admin is off, the backend always stores "Administrator"
  // regardless of what was last typed here, that's not a useful starting
  // point if the operator flips the toggle on, offer the real default instead.
  const [localAdminUsername, setLocalAdminUsername] = useState(
    existing && existing.local_admin_username !== "Administrator" ? existing.local_admin_username : "svcadmin"
  );
  const [localAdminPassword, setLocalAdminPassword] = useState("");
  const [domainJoinEnabled, setDomainJoinEnabled] = useState(existing?.domain_join_enabled ?? false);
  const [domainFqdn, setDomainFqdn] = useState(existing?.domain_fqdn ?? "");
  const [domainJoinAccount, setDomainJoinAccount] = useState(existing?.domain_join_account ?? "");
  const [domainJoinCredential, setDomainJoinCredential] = useState("");
  const [windowsFeatures, setWindowsFeatures] = useState<string[]>(existing?.windows_features ?? []);
  const [appInstalls, setAppInstalls] = useState<AppInstallEntry[]>(existing?.app_installs ?? []);
  const [appToAdd, setAppToAdd] = useState(appAssets[0]?.id ?? "");
  const [error, setError] = useState<string | null>(null);

  function addAppInstall() {
    if (!appToAdd || appInstalls.some((e) => e.app_asset_id === appToAdd)) return;
    setAppInstalls((prev) => [...prev, { app_asset_id: appToAdd, install_args: "" }]);
    const remaining = appAssets.filter((a) => a.id !== appToAdd && !appInstalls.some((e) => e.app_asset_id === a.id));
    setAppToAdd(remaining[0]?.id ?? "");
  }

  function removeAppInstall(appAssetId: string) {
    setAppInstalls((prev) => prev.filter((e) => e.app_asset_id !== appAssetId));
  }

  function moveAppInstall(index: number, direction: -1 | 1) {
    setAppInstalls((prev) => {
      const next = [...prev];
      const target = index + direction;
      if (target < 0 || target >= next.length) return prev;
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  }

  function setAppInstallArgs(appAssetId: string, args: string) {
    setAppInstalls((prev) => prev.map((e) => (e.app_asset_id === appAssetId ? { ...e, install_args: args } : e)));
  }

  function toggleFeature(name: string) {
    setWindowsFeatures((prev) => (prev.includes(name) ? prev.filter((f) => f !== name) : [...prev, name]));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const missing: string[] = [];
    if (!name) missing.push("Name");
    if (!diskLayoutId) missing.push("Disk layout");
    if (!networkName) missing.push("Network name");
    if (customAdminEnabled && !localAdminUsername) missing.push("Local admin username");
    if (!isEdit && !localAdminPassword) missing.push("Local administrator password");
    if (missing.length > 0) {
      setError(`Missing required field${missing.length > 1 ? "s" : ""}: ${missing.join(", ")}.`);
      return;
    }
    if (customAdminEnabled && localAdminUsername.trim().toLowerCase() === "administrator") {
      setError('Local admin username can\'t be "Administrator", DeployCore disables that built-in account, pick a different name.');
      return;
    }
    const body = {
      name,
      iso_asset_id: isoAssetId || null,
      image_index: imageIndex,
      disk_layout_id: diskLayoutId,
      cpu_count: cpuCount,
      cores_per_socket: coresPerSocket,
      ram_mb: ramMb,
      disk_size_gb: diskSizeGb,
      disk_provisioning: diskProvisioning,
      network_name: networkName,
      network_adapter_type: networkAdapterType,
      locale,
      timezone,
      keyboard_layout: keyboardLayout,
      custom_admin_enabled: customAdminEnabled,
      local_admin_username: localAdminUsername,
      local_admin_password: localAdminPassword,
      domain_join_enabled: domainJoinEnabled,
      domain_fqdn: domainJoinEnabled ? domainFqdn : null,
      domain_join_account: domainJoinEnabled ? domainJoinAccount : null,
      domain_join_credential: domainJoinEnabled ? domainJoinCredential : null,
      windows_features: windowsFeatures,
      post_install_scripts: existing?.post_install_scripts ?? [],
      app_installs: appInstalls,
    };
    try {
      if (isEdit) {
        await api.patch(`/organizations/${orgId}/templates/${existing!.id}`, body);
      } else {
        await api.post(`/organizations/${orgId}/templates`, body);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save template.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/30 py-8">
      <form onSubmit={onSubmit} className="max-h-[85vh] w-[32rem] overflow-y-auto rounded-lg border border-neutral-200 bg-white p-5 shadow-sm dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-1 text-sm font-semibold">{isEdit ? `Edit ${existing!.name}` : "New template"}</h2>
        {isEdit && (
          <p className="mb-4 text-xs text-neutral-500">
            Changes apply to deployments created from this template afterward. Deployments already completed or
            in progress are not affected.
          </p>
        )}
        {!isEdit && <div className="mb-4" />}

        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Name</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={name} onChange={(e) => setName(e.target.value)} />

        <div className="mb-3 grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Windows ISO</label>
            <Select
              className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm"
              value={isoAssetId}
              onChange={(e) => {
                setIsoAssetId(e.target.value);
                const editions = isoAssets.find((iso) => iso.id === e.target.value)?.windows_editions ?? [];
                if (editions.length > 0 && !editions.some((ed) => ed.index === imageIndex)) {
                  setImageIndex(editions[0].index);
                }
              }}
            >
              <option value="">None yet, cannot deploy</option>
              {isoAssets.map((iso) => (
                <option key={iso.id} value={iso.id}>{iso.filename}</option>
              ))}
            </Select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Disk layout</label>
            <Select className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" value={diskLayoutId} onChange={(e) => setDiskLayoutId(e.target.value)}>
              <option value="">Select...</option>
              {diskLayouts.map((l) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </Select>
          </div>
        </div>

        {(() => {
          const editions = isoAssets.find((iso) => iso.id === isoAssetId)?.windows_editions ?? [];
          return (
            <div className="mb-3">
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Windows edition</label>
              {editions.length > 0 ? (
                <Select
                  className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm"
                  value={String(imageIndex)}
                  onChange={(e) => setImageIndex(Number(e.target.value))}
                >
                  {editions.map((ed) => (
                    <option key={ed.index} value={ed.index}>{ed.index} — {ed.name || ed.description}</option>
                  ))}
                </Select>
              ) : (
                <input
                  type="number"
                  min={1}
                  className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                  value={imageIndex}
                  onChange={(e) => setImageIndex(Number(e.target.value))}
                  placeholder="/IMAGE/INDEX (no editions detected in this ISO)"
                />
              )}
            </div>
          );
        })()}

        <div className="mb-3 grid grid-cols-4 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">vCPU</label>
            <input type="number" className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={cpuCount} onChange={(e) => setCpuCount(Number(e.target.value))} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Cores/socket</label>
            <input type="number" min={1} className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={coresPerSocket} onChange={(e) => setCoresPerSocket(Number(e.target.value))} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">RAM (MB)</label>
            <input type="number" className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={ramMb} onChange={(e) => setRamMb(Number(e.target.value))} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Disk (GB)</label>
            <input type="number" className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={diskSizeGb} onChange={(e) => setDiskSizeGb(Number(e.target.value))} />
          </div>
        </div>

        <div className="mb-3">
          <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Disk provisioning</label>
          <Select className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" value={diskProvisioning} onChange={(e) => setDiskProvisioning(e.target.value as DiskProvisioning)}>
            <option value="thin">Thin provision</option>
            <option value="thick_lazy_zeroed">Thick provision, lazily zeroed</option>
            <option value="thick_eager_zeroed">Thick provision, eagerly zeroed</option>
          </Select>
        </div>

        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Network name (ESXi/vCenter port group, not a Windows name)</label>
        <div className="mb-3 grid grid-cols-2 gap-3">
          <input required className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={networkName} onChange={(e) => setNetworkName(e.target.value)} />
          <Select className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" value={networkAdapterType} onChange={(e) => setNetworkAdapterType(e.target.value as NetworkAdapterType)}>
            <option value="vmxnet3">VMXNET3</option>
            <option value="e1000">E1000</option>
            <option value="e1000e">E1000E</option>
          </Select>
        </div>

        <div className="mb-3 grid grid-cols-3 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Locale (Windows id, not IETF)</label>
            <input className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={locale} onChange={(e) => setLocale(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Timezone (Windows id, not IANA)</label>
            <input className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Keyboard layout</label>
            <input className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={keyboardLayout} onChange={(e) => setKeyboardLayout(e.target.value)} />
          </div>
        </div>

        <label className="mb-1 flex items-center gap-2 text-xs font-medium text-neutral-600 dark:text-neutral-400">
          <input type="checkbox" checked={customAdminEnabled} onChange={(e) => setCustomAdminEnabled(e.target.checked)} />
          Custom local administrator account
        </label>
        {customAdminEnabled && (
          <p className="mb-1 text-xs text-neutral-400">Disables the built-in Administrator account.</p>
        )}
        <div className={`mb-3 grid gap-3 ${customAdminEnabled ? "grid-cols-2" : "grid-cols-1"}`}>
          {customAdminEnabled && (
            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Local admin username</label>
              <input
                required
                className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                value={localAdminUsername}
                onChange={(e) => setLocalAdminUsername(e.target.value)}
              />
            </div>
          )}
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">
              {customAdminEnabled ? "Local admin password" : "Administrator password"}{isEdit && " (leave blank to keep unchanged)"}
            </label>
            <input
              required={!isEdit}
              type="password"
              autoComplete="new-password"
              className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={localAdminPassword}
              onChange={(e) => setLocalAdminPassword(e.target.value)}
            />
          </div>
        </div>

        <label className="mb-2 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Windows roles and features</label>
        <div className="mb-3 grid grid-cols-2 gap-x-3 gap-y-1 rounded-md border border-neutral-200 p-3 dark:border-neutral-700">
          {WINDOWS_FEATURES.map((f) => (
            <label key={f.name} className="flex items-center gap-1.5 text-xs text-neutral-700 dark:text-neutral-300">
              <input
                type="checkbox"
                checked={windowsFeatures.includes(f.name)}
                onChange={() => toggleFeature(f.name)}
              />
              {f.label}
            </label>
          ))}
        </div>

        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Software to install</label>
        {appInstalls.length > 0 && (
          <div className="mb-2 space-y-1.5">
            {appInstalls.map((entry, index) => {
              const app = appAssets.find((a) => a.id === entry.app_asset_id);
              return (
                <div key={entry.app_asset_id} className="flex items-center gap-2 rounded-md border border-neutral-200 p-2 dark:border-neutral-700">
                  <span className="w-40 shrink-0 truncate text-xs font-medium text-neutral-700 dark:text-neutral-300">
                    {app?.name ?? "(deleted app asset)"}
                  </span>
                  <input
                    className="min-w-0 flex-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs dark:bg-neutral-900"
                    placeholder={app ? `Default: ${app.default_install_args || "(none)"}` : "install args"}
                    value={entry.install_args}
                    onChange={(e) => setAppInstallArgs(entry.app_asset_id, e.target.value)}
                  />
                  <div className="flex shrink-0 gap-1">
                    <button type="button" className="rounded border border-neutral-300 px-1.5 py-0.5 text-xs disabled:opacity-30 dark:border-neutral-700" disabled={index === 0} onClick={() => moveAppInstall(index, -1)}>
                      ↑
                    </button>
                    <button type="button" className="rounded border border-neutral-300 px-1.5 py-0.5 text-xs disabled:opacity-30 dark:border-neutral-700" disabled={index === appInstalls.length - 1} onClick={() => moveAppInstall(index, 1)}>
                      ↓
                    </button>
                    <button type="button" className="rounded border border-red-200 px-1.5 py-0.5 text-xs text-red-700 dark:border-red-900 dark:text-red-400" onClick={() => removeAppInstall(entry.app_asset_id)}>
                      <Trash2 size={12} strokeWidth={1.75} />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <div className="mb-3 flex gap-2">
          <Select
            className="flex-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm"
            value={appToAdd}
            onChange={(e) => setAppToAdd(e.target.value)}
          >
            {appAssets.length === 0 && <option value="">No app assets uploaded yet</option>}
            {appAssets
              .filter((a) => !appInstalls.some((e) => e.app_asset_id === a.id))
              .map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
          </Select>
          <button type="button" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm dark:border-neutral-700" onClick={addAppInstall} disabled={!appToAdd}>
            Add
          </button>
        </div>

        <label className="mb-2 flex items-center gap-2 text-xs font-medium text-neutral-600 dark:text-neutral-400">
          <input type="checkbox" checked={domainJoinEnabled} onChange={(e) => setDomainJoinEnabled(e.target.checked)} />
          Join a domain
        </label>
        {domainJoinEnabled && (
          <div className="mb-3 grid grid-cols-2 gap-3">
            <input placeholder="Domain FQDN" autoComplete="off" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={domainFqdn} onChange={(e) => setDomainFqdn(e.target.value)} />
            <input placeholder="Join account" autoComplete="off" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={domainJoinAccount} onChange={(e) => setDomainJoinAccount(e.target.value)} />
            <input
              placeholder={isEdit ? "Join password (leave blank to keep unchanged)" : "Join password"}
              type="password"
              autoComplete="new-password"
              className="col-span-2 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={domainJoinCredential}
              onChange={(e) => setDomainJoinCredential(e.target.value)}
            />
          </div>
        )}

        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" onClick={onClose}>Cancel</button>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">{isEdit ? "Save" : "Create"}</button>
        </div>
      </form>
    </div>
  );
}
