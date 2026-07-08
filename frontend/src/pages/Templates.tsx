import { Copy, Pencil, Plus } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import DataTable from "../components/DataTable";
import { DeploymentTemplate, DiskLayout, IsoAsset } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Templates() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [templates, setTemplates] = useState<DeploymentTemplate[]>([]);
  const [diskLayouts, setDiskLayouts] = useState<DiskLayout[]>([]);
  const [isoAssets, setIsoAssets] = useState<IsoAsset[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<DeploymentTemplate | null>(null);

  async function load() {
    if (!selectedOrgId) return;
    const [t, d, i] = await Promise.all([
      api.get<DeploymentTemplate[]>(`/organizations/${selectedOrgId}/templates`),
      api.get<DiskLayout[]>(`/organizations/${selectedOrgId}/disk-layouts`),
      api.get<IsoAsset[]>(`/organizations/${selectedOrgId}/iso-assets`),
    ]);
    setTemplates(t);
    setDiskLayouts(d);
    setIsoAssets(i.filter((iso) => iso.kind === "windows_iso"));
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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Templates</h1>
        {canManage && (
          <button
            className="flex items-center gap-1.5 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={15} strokeWidth={2} />
            New template
          </button>
        )}
      </div>

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
          { key: "sizing", header: "CPU / RAM / Disk", render: (t) => `${t.cpu_count} vCPU / ${t.ram_mb} MB / ${t.disk_size_gb} GB` },
          { key: "domain", header: "Domain join", render: (t) => (t.domain_join_enabled ? t.domain_fqdn : "Workgroup") },
          { key: "features", header: "Roles", render: (t) => t.windows_features.join(", ") || "—" },
          {
            key: "actions",
            header: "",
            render: (t) =>
              canManage && (
                <div className="flex items-center gap-1.5">
                  {t.org_id === selectedOrgId && (
                    <button
                      className="flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50"
                      onClick={() => setEditing(t)}
                    >
                      <Pencil size={12} strokeWidth={1.75} />
                      Edit
                    </button>
                  )}
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50"
                    onClick={() => cloneTemplate(t.id)}
                  >
                    <Copy size={12} strokeWidth={1.75} />
                    Clone
                  </button>
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
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            await load();
          }}
        />
      )}
    </div>
  );
}

function TemplateForm({
  orgId,
  diskLayouts,
  isoAssets,
  existing,
  onClose,
  onSaved,
}: {
  orgId: string;
  diskLayouts: DiskLayout[];
  isoAssets: IsoAsset[];
  existing?: DeploymentTemplate;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!existing;
  const [name, setName] = useState(existing?.name ?? "");
  const [isoAssetId, setIsoAssetId] = useState(existing?.iso_asset_id ?? "");
  const [diskLayoutId, setDiskLayoutId] = useState(existing?.disk_layout_id ?? "");
  const [cpuCount, setCpuCount] = useState(existing?.cpu_count ?? 2);
  const [ramMb, setRamMb] = useState(existing?.ram_mb ?? 4096);
  const [diskSizeGb, setDiskSizeGb] = useState(existing?.disk_size_gb ?? 80);
  const [networkName, setNetworkName] = useState(existing?.network_name ?? "");
  const [localAdminPassword, setLocalAdminPassword] = useState("");
  const [domainJoinEnabled, setDomainJoinEnabled] = useState(existing?.domain_join_enabled ?? false);
  const [domainFqdn, setDomainFqdn] = useState(existing?.domain_fqdn ?? "");
  const [domainJoinAccount, setDomainJoinAccount] = useState(existing?.domain_join_account ?? "");
  const [domainJoinCredential, setDomainJoinCredential] = useState("");
  const [windowsFeatures, setWindowsFeatures] = useState(existing?.windows_features.join(", ") ?? "");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const body = {
      name,
      iso_asset_id: isoAssetId || null,
      disk_layout_id: diskLayoutId,
      cpu_count: cpuCount,
      ram_mb: ramMb,
      disk_size_gb: diskSizeGb,
      network_name: networkName,
      local_admin_password: localAdminPassword,
      domain_join_enabled: domainJoinEnabled,
      domain_fqdn: domainJoinEnabled ? domainFqdn : null,
      domain_join_account: domainJoinEnabled ? domainJoinAccount : null,
      domain_join_credential: domainJoinEnabled ? domainJoinCredential : null,
      windows_features: windowsFeatures
        .split(",")
        .map((f) => f.trim())
        .filter(Boolean),
      post_install_scripts: existing?.post_install_scripts ?? [],
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
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/30 py-8">
      <form onSubmit={onSubmit} className="w-[32rem] rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">{isEdit ? `Edit ${existing!.name}` : "New template"}</h2>

        <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={name} onChange={(e) => setName(e.target.value)} />

        <div className="mb-3 grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Windows ISO</label>
            <select className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={isoAssetId} onChange={(e) => setIsoAssetId(e.target.value)}>
              <option value="">None yet — cannot deploy</option>
              {isoAssets.map((iso) => (
                <option key={iso.id} value={iso.id}>{iso.filename}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Disk layout</label>
            <select required className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={diskLayoutId} onChange={(e) => setDiskLayoutId(e.target.value)}>
              <option value="">Select...</option>
              {diskLayouts.map((l) => (
                <option key={l.id} value={l.id}>{l.name}</option>
              ))}
            </select>
          </div>
        </div>

        <div className="mb-3 grid grid-cols-3 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">vCPU</label>
            <input type="number" className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={cpuCount} onChange={(e) => setCpuCount(Number(e.target.value))} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">RAM (MB)</label>
            <input type="number" className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={ramMb} onChange={(e) => setRamMb(Number(e.target.value))} />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">Disk (GB)</label>
            <input type="number" className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={diskSizeGb} onChange={(e) => setDiskSizeGb(Number(e.target.value))} />
          </div>
        </div>

        <label className="mb-1 block text-xs font-medium text-neutral-600">Network name</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={networkName} onChange={(e) => setNetworkName(e.target.value)} />

        <label className="mb-1 block text-xs font-medium text-neutral-600">
          Local administrator password{isEdit && " (leave blank to keep unchanged)"}
        </label>
        <input
          required={!isEdit}
          type="password"
          className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={localAdminPassword}
          onChange={(e) => setLocalAdminPassword(e.target.value)}
        />

        <label className="mb-1 block text-xs font-medium text-neutral-600">Windows features (comma-separated)</label>
        <input placeholder="Web-Server, DNS, DHCP" className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={windowsFeatures} onChange={(e) => setWindowsFeatures(e.target.value)} />

        <label className="mb-2 flex items-center gap-2 text-xs font-medium text-neutral-600">
          <input type="checkbox" checked={domainJoinEnabled} onChange={(e) => setDomainJoinEnabled(e.target.checked)} />
          Join a domain
        </label>
        {domainJoinEnabled && (
          <div className="mb-3 grid grid-cols-2 gap-3">
            <input placeholder="Domain FQDN" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={domainFqdn} onChange={(e) => setDomainFqdn(e.target.value)} />
            <input placeholder="Join account" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={domainJoinAccount} onChange={(e) => setDomainJoinAccount(e.target.value)} />
            <input
              placeholder={isEdit ? "Join password (leave blank to keep unchanged)" : "Join password"}
              type="password"
              className="col-span-2 rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
              value={domainJoinCredential}
              onChange={(e) => setDomainJoinCredential(e.target.value)}
            />
          </div>
        )}

        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" onClick={onClose}>Cancel</button>
          <button type="submit" className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white">{isEdit ? "Save" : "Create"}</button>
        </div>
      </form>
    </div>
  );
}
