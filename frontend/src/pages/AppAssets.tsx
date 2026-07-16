import { MonitorSmartphone, Pencil, RefreshCw, Trash2, UploadCloud } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, getToken } from "../api/client";
import Badge from "../components/Badge";
import ConfirmDialog from "../components/ConfirmDialog";
import DataTable from "../components/DataTable";
import FileDropzone from "../components/FileDropzone";
import Select from "../components/Select";
import { AppAsset, AppKind } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

const CHUNK_SIZE = 8 * 1024 * 1024;

export default function AppAssets() {
  const { selectedOrgId, selectedOrg, loaded: orgLoaded } = useOrg();
  const { user, effectiveRole } = useAuth();
  const [apps, setApps] = useState<AppAsset[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [showUpload, setShowUpload] = useState(false);
  const [editApp, setEditApp] = useState<AppAsset | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<AppAsset | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [remoteAgentError, setRemoteAgentError] = useState<string | null>(null);
  const [refreshingAgent, setRefreshingAgent] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  const isGlobalAdmin = !!user && roleAtLeast(user.global_role, "admin");

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  async function setRemoteAgent(app: AppAsset, enabled: boolean) {
    setRemoteAgentError(null);
    try {
      await api.post(`/app-assets/global/${app.id}/set-remote-agent`, { enabled });
      await load();
    } catch (err) {
      setRemoteAgentError(err instanceof ApiError ? err.message : "Failed to update.");
    }
  }

  async function refreshAgent() {
    setRemoteAgentError(null);
    setRefreshingAgent(true);
    try {
      const asset = await api.post<AppAsset>("/app-assets/global/refresh-agent");
      await load();
      // Show a checksum prefix, not just size - confirmed live that two
      // genuinely different builds can compress to the exact same byte
      // count, which made size alone useless as proof a refresh actually
      // fetched new content (it can honestly report "current" at a size
      // that hasn't changed in three builds). A checksum can't coincide
      // the same way, so it's the only thing actually worth comparing
      // against a fresh CI run to confirm this is really the build you
      // expect, not the previous one.
      const hash = asset.checksum_sha256 ? asset.checksum_sha256.slice(0, 12) : "unknown";
      setToast(`Agent installer refreshed - checksum ${hash}...`);
    } catch (err) {
      setRemoteAgentError(err instanceof ApiError ? err.message : "Failed to check for a newer agent installer.");
    } finally {
      setRefreshingAgent(false);
    }
  }

  async function load() {
    if (!selectedOrgId) return;
    try {
      setApps(await api.get<AppAsset[]>(`/organizations/${selectedOrgId}/app-assets`));
    } finally {
      setLoaded(true);
    }
  }

  async function deleteApp() {
    if (!confirmDelete) return;
    setDeleteError(null);
    try {
      if (confirmDelete.org_id) {
        await api.delete(`/organizations/${confirmDelete.org_id}/app-assets/${confirmDelete.id}`);
      } else {
        await api.delete(`/app-assets/global/${confirmDelete.id}`);
      }
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "Failed to delete this app.");
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  if (!orgLoaded) return null;
  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;
  const canManage = roleAtLeast(effectiveRole(selectedOrgId), "operator");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">App Assets</h1>
          <p className="text-xs text-neutral-500">
            MSI/EXE installers that templates can install automatically over WinRM after Windows Setup finishes.
          </p>
        </div>
        {canManage && (
          <button
            className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            onClick={() => setShowUpload(true)}
          >
            <UploadCloud size={15} strokeWidth={1.75} />
            Upload App
          </button>
        )}
      </div>

      {remoteAgentError && (
        <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {remoteAgentError}
        </div>
      )}

      {toast && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-400">
          {toast}
        </div>
      )}

      <DataTable<AppAsset>
        rows={apps}
        loading={!loaded}
        rowKey={(a) => a.id}
        searchValue={(a) => `${a.name} ${a.filename}`}
        columns={[
          { key: "name", header: "Name", render: (a) => <span className="whitespace-nowrap">{a.name}</span>, sortValue: (a) => a.name },
          { key: "filename", header: "File", render: (a) => <span className="whitespace-nowrap">{a.filename}</span> },
          { key: "kind", header: "Kind", render: (a) => a.kind.toUpperCase(), shrink: true },
          {
            key: "args",
            header: "Default install args",
            render: (a) => <code className="text-xs">{a.default_install_args || "(none)"}</code>,
          },
          {
            key: "scope",
            header: "Scope",
            render: (a) => (
              <div className="flex items-center gap-1.5 whitespace-nowrap">
                {a.org_id ? selectedOrg?.name ?? "Organization" : "Global"}
                {a.is_remote_agent && (
                  <span
                    className="flex shrink-0 items-center gap-1 whitespace-nowrap rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-700 dark:bg-blue-950 dark:text-blue-400"
                    title="This is the DeployCore Remote Management Agent installer"
                  >
                    <MonitorSmartphone size={11} strokeWidth={1.75} />
                    Remote Agent
                  </span>
                )}
              </div>
            ),
          },
          { key: "size", header: "Size", render: (a) => (a.size_bytes ? `${(a.size_bytes / 1e6).toFixed(1)} MB` : "(unknown)") },
          { key: "status", header: "Status", render: (a) => <Badge value={a.upload_status} />, shrink: true },
          {
            key: "actions",
            header: "",
            render: (a) =>
              (a.org_id ? canManage && a.org_id === selectedOrgId : isGlobalAdmin) && (
                <div className="flex items-center gap-1.5 whitespace-nowrap">
                  {!a.org_id && isGlobalAdmin && a.upload_status === "complete" && (
                    <button
                      className="flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                      title={a.is_remote_agent ? "Unset as Remote Management Agent" : "Set as the Remote Management Agent installer"}
                      onClick={() => setRemoteAgent(a, !a.is_remote_agent)}
                    >
                      <MonitorSmartphone size={12} strokeWidth={1.75} />
                      {a.is_remote_agent ? "Unset agent" : "Set as agent"}
                    </button>
                  )}
                  {!a.org_id && isGlobalAdmin && a.is_remote_agent && (
                    <button
                      className="flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                      title="Check GitHub for a newer agent build now, instead of waiting for the next api restart"
                      onClick={refreshAgent}
                      disabled={refreshingAgent}
                    >
                      <RefreshCw size={12} strokeWidth={1.75} className={refreshingAgent ? "animate-spin" : undefined} />
                      {refreshingAgent ? "Checking..." : "Check for update"}
                    </button>
                  )}
                  <button
                    className="flex shrink-0 items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                    onClick={() => setEditApp(a)}
                  >
                    <Pencil size={12} strokeWidth={1.75} />
                  </button>
                  <button
                    className="flex shrink-0 items-center gap-1 rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                    onClick={() => {
                      setDeleteError(null);
                      setConfirmDelete(a);
                    }}
                  >
                    <Trash2 size={12} strokeWidth={1.75} />
                  </button>
                </div>
              ),
          },
        ]}
      />

      {showUpload && (
        <UploadAppForm
          orgId={selectedOrgId}
          allowGlobal={isGlobalAdmin}
          onClose={() => setShowUpload(false)}
          onCreated={load}
          onDone={async () => {
            setShowUpload(false);
            await load();
          }}
        />
      )}

      {editApp && (
        <EditAppForm
          app={editApp}
          onClose={() => setEditApp(null)}
          onDone={async () => {
            setEditApp(null);
            await load();
          }}
        />
      )}

      <ConfirmDialog
        open={!!confirmDelete}
        title="Delete app asset"
        message={
          <>
            {confirmDelete?.org_id
              ? `Delete "${confirmDelete?.name}"? Templates listing it will just skip it at deploy time. This cannot be undone.`
              : `Delete "${confirmDelete?.name}"? This is a global app asset — templates in every organization listing it will just skip it at deploy time. This cannot be undone.`}
            {deleteError && <div className="mt-2 text-red-600 dark:text-red-400">{deleteError}</div>}
          </>
        }
        confirmLabel="Delete"
        onConfirm={deleteApp}
        onCancel={() => {
          setDeleteError(null);
          setConfirmDelete(null);
        }}
      />
    </div>
  );
}

function inferKind(filename: string): AppKind {
  return filename.toLowerCase().endsWith(".msi") ? "msi" : "exe";
}

function UploadAppForm({
  orgId,
  allowGlobal,
  onClose,
  onCreated,
  onDone,
}: {
  orgId: string;
  allowGlobal: boolean;
  onClose: () => void;
  onCreated: () => void;
  onDone: () => void;
}) {
  const [scope, setScope] = useState<"org" | "global">("org");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<AppKind>("exe");
  const [installArgs, setInstallArgs] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function onSelectFile(f: File) {
    setFile(f);
    setKind(inferKind(f.name));
    if (!name) setName(f.name.replace(/\.(msi|exe)$/i, ""));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file || !name) return;
    setError(null);
    setUploading(true);
    try {
      const createPath = scope === "global" ? "/app-assets/global" : `/organizations/${orgId}/app-assets`;
      const chunkBase = scope === "global" ? "/api/app-assets/global" : `/api/organizations/${orgId}/app-assets`;
      const finalizePath = scope === "global" ? "/app-assets/global" : `/organizations/${orgId}/app-assets`;

      const app = await api.post<AppAsset>(createPath, {
        name,
        filename: file.name,
        kind,
        default_install_args: installArgs,
      });
      onCreated();
      let offset = 0;
      while (offset < file.size) {
        const chunk = file.slice(offset, offset + CHUNK_SIZE);
        const res = await fetch(`${chunkBase}/${app.id}/chunk`, {
          method: "POST",
          headers: { Authorization: `Bearer ${getToken()}`, "Content-Type": "application/octet-stream" },
          body: chunk,
        });
        if (!res.ok) throw new Error(`Chunk upload failed at offset ${offset}`);
        if (offset === 0) onCreated();
        offset += CHUNK_SIZE;
        setProgress(Math.min(100, Math.round((offset / file.size) * 100)));
      }
      await api.post(`${finalizePath}/${app.id}/finalize`);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">Upload App</h2>
        {allowGlobal && (
          <>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Available to</label>
            <Select
              className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm"
              value={scope}
              onChange={(e) => setScope(e.target.value as "org" | "global")}
            >
              <option value="org">This organization only</option>
              <option value="global">Every organization (global)</option>
            </Select>
          </>
        )}
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">File</label>
        <div className="mb-3">
          <FileDropzone accept=".msi,.exe" fileName={file?.name} hint="MSI or EXE installers only" onSelect={onSelectFile} />
        </div>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Display name</label>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Datto RMM Agent"
        />
        <div className="mb-3 grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Kind</label>
            <Select
              className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm"
              value={kind}
              onChange={(e) => setKind(e.target.value as AppKind)}
            >
              <option value="exe">EXE</option>
              <option value="msi">MSI</option>
            </Select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Default silent-install args</label>
            <input
              className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={installArgs}
              onChange={(e) => setInstallArgs(e.target.value)}
              placeholder={kind === "msi" ? "/qn /norestart" : "/S"}
            />
          </div>
        </div>
        {uploading && (
          <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
            <div className="h-full bg-blue-600 transition-all" style={{ width: `${progress}%` }} />
          </div>
        )}
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" onClick={onClose} disabled={uploading}>
            Cancel
          </button>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50" disabled={uploading || !file || !name}>
            {uploading ? `Uploading... ${progress}%` : "Upload"}
          </button>
        </div>
      </form>
    </div>
  );
}

function EditAppForm({ app, onClose, onDone }: { app: AppAsset; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(app.name);
  const [kind, setKind] = useState<AppKind>(app.kind);
  const [installArgs, setInstallArgs] = useState(app.default_install_args);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name) return;
    setError(null);
    setSaving(true);
    try {
      const path = app.org_id ? `/organizations/${app.org_id}/app-assets/${app.id}` : `/app-assets/global/${app.id}`;
      await api.patch(path, { name, kind, default_install_args: installArgs });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">Edit App Asset</h2>
        <p className="mb-3 text-xs text-neutral-500">
          File: <code>{app.filename}</code> ({app.org_id ? "org-scoped" : "global"}) - re-upload as a new asset to
          replace the file itself.
        </p>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Display name</label>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <div className="mb-3 grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Kind</label>
            <Select
              className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm"
              value={kind}
              onChange={(e) => setKind(e.target.value as AppKind)}
            >
              <option value="exe">EXE</option>
              <option value="msi">MSI</option>
            </Select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Default silent-install args</label>
            <input
              className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={installArgs}
              onChange={(e) => setInstallArgs(e.target.value)}
              placeholder={kind === "msi" ? "/qn /norestart" : "/S"}
            />
          </div>
        </div>
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50" disabled={saving || !name}>
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </form>
    </div>
  );
}
