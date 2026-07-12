import { Copy, Download, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import ConfirmDialog from "../components/ConfirmDialog";
import DataTable from "../components/DataTable";
import TemplateFieldsForm, { TemplateFieldsBody } from "../components/TemplateFieldsForm";
import { downloadJson, readJsonFile } from "../lib/jsonFile";
import { AppAsset, DeploymentTemplate, DiskLayout, HypervisorHost, IsoAsset } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Templates() {
  const { selectedOrgId, loaded: orgLoaded } = useOrg();
  const { effectiveRole } = useAuth();
  const [templates, setTemplates] = useState<DeploymentTemplate[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [diskLayouts, setDiskLayouts] = useState<DiskLayout[]>([]);
  const [isoAssets, setIsoAssets] = useState<IsoAsset[]>([]);
  const [appAssets, setAppAssets] = useState<AppAsset[]>([]);
  const [hosts, setHosts] = useState<HypervisorHost[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<DeploymentTemplate | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<DeploymentTemplate | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const importInputRef = useRef<HTMLInputElement>(null);

  async function load() {
    if (!selectedOrgId) return;
    try {
      const [t, d, i, a, h] = await Promise.all([
        api.get<DeploymentTemplate[]>(`/organizations/${selectedOrgId}/templates`),
        api.get<DiskLayout[]>(`/organizations/${selectedOrgId}/disk-layouts`),
        api.get<IsoAsset[]>(`/organizations/${selectedOrgId}/iso-assets`),
        api.get<AppAsset[]>(`/organizations/${selectedOrgId}/app-assets`),
        api.get<HypervisorHost[]>(`/organizations/${selectedOrgId}/hypervisors`),
      ]);
      setTemplates(t);
      setDiskLayouts(d);
      setIsoAssets(i.filter((iso) => iso.kind === "windows_iso"));
      setAppAssets(a);
      setHosts(h);
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  if (!orgLoaded) return null;
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
        loading={!loaded}
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
          hosts={hosts}
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
          hosts={hosts}
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
  hosts,
  diskLayouts,
  isoAssets,
  appAssets,
  existing,
  onClose,
  onSaved,
}: {
  orgId: string;
  hosts: HypervisorHost[];
  diskLayouts: DiskLayout[];
  isoAssets: IsoAsset[];
  appAssets: AppAsset[];
  existing?: DeploymentTemplate;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!existing;

  async function handleSubmit(body: TemplateFieldsBody) {
    try {
      if (isEdit) {
        await api.patch(`/organizations/${orgId}/templates/${existing!.id}`, body);
      } else {
        await api.post(`/organizations/${orgId}/templates`, body);
      }
    } catch (err) {
      throw new Error(err instanceof ApiError ? err.message : "Failed to save template.");
    }
    onSaved();
  }

  return (
    <TemplateFieldsForm
      orgId={orgId}
      hosts={hosts}
      diskLayouts={diskLayouts}
      isoAssets={isoAssets}
      appAssets={appAssets}
      existing={existing}
      title={isEdit ? `Edit ${existing!.name}` : "New template"}
      description={
        isEdit
          ? "Changes apply to deployments created from this template afterward. Deployments already completed or in progress are not affected."
          : undefined
      }
      requirePassword={!isEdit}
      submitLabel={isEdit ? "Save" : "Create"}
      onClose={onClose}
      onSubmit={handleSubmit}
    />
  );
}
