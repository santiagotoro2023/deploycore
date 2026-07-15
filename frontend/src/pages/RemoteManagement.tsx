import { AlertTriangle, CheckCircle2, Copy, Download, MonitorSmartphone, Pencil, Plus, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import ConfirmDialog from "../components/ConfirmDialog";
import DataTable from "../components/DataTable";
import { ManagedHost, RemoteStatus } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function RemoteManagement() {
  const { selectedOrgId, loaded: orgLoaded } = useOrg();
  const { effectiveRole } = useAuth();
  const navigate = useNavigate();
  const [hosts, setHosts] = useState<ManagedHost[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [editHost, setEditHost] = useState<ManagedHost | null>(null);
  const [installHost, setInstallHost] = useState<ManagedHost | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<ManagedHost | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [status, setStatus] = useState<RemoteStatus | null>(null);

  async function load() {
    if (!selectedOrgId) return;
    try {
      setHosts(await api.get<ManagedHost[]>(`/organizations/${selectedOrgId}/managed-hosts`));
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  useEffect(() => {
    api.get<RemoteStatus>("/remote/status").then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  if (!orgLoaded) return null;
  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;
  const canManage = roleAtLeast(effectiveRole(selectedOrgId), "operator");

  async function deleteHost() {
    if (!confirmDelete || !selectedOrgId) return;
    setDeleteError(null);
    try {
      await api.delete(`/organizations/${selectedOrgId}/managed-hosts/${confirmDelete.id}`);
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "Failed to remove this host.");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Remote Management</h1>
          <p className="text-xs text-neutral-500">
            Connect to any enrolled server or workstation's screen, right from the browser.
          </p>
        </div>
        {canManage && (
          <button
            className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            onClick={() => setShowAdd(true)}
          >
            <Plus size={15} strokeWidth={2} />
            Add host
          </button>
        )}
      </div>

      {toast && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-400">
          {toast}
        </div>
      )}

      {status && <RemoteSetupBanner status={status} />}

      <DataTable<ManagedHost>
        rows={hosts}
        loading={!loaded}
        rowKey={(h) => h.id}
        searchValue={(h) => h.name}
        columns={[
          { key: "name", header: "Name", render: (h) => h.name, sortValue: (h) => h.name },
          {
            key: "status",
            header: "Status",
            render: (h) => <Badge value={h.enrolled ? "ok" : "pending"} />,
            shrink: true,
          },
          {
            key: "last_seen",
            header: "Last seen",
            render: (h) => (h.last_seen_at ? new Date(h.last_seen_at).toLocaleString() : "Never"),
          },
          { key: "source", header: "Source", render: (h) => (h.deployment_id ? "Deployed by DeployCore" : "Standalone") },
          {
            key: "actions",
            header: "",
            render: (h) => (
              <div className="flex items-center gap-1.5">
                <button
                  className="flex items-center gap-1 rounded-md bg-blue-600 px-2 py-1 text-xs text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={!h.enrolled}
                  title={h.enrolled ? "Connect" : "Waiting for the agent to enroll"}
                  onClick={() => navigate(`/remote-management/${h.id}`)}
                >
                  <MonitorSmartphone size={12} strokeWidth={1.75} />
                  Connect
                </button>
                {canManage && !h.enrolled && (
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                    onClick={() => setInstallHost(h)}
                  >
                    <Download size={12} strokeWidth={1.75} />
                    Install command
                  </button>
                )}
                {canManage && (
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                    onClick={() => setEditHost(h)}
                  >
                    <Pencil size={12} strokeWidth={1.75} />
                  </button>
                )}
                {canManage && (
                  <button
                    className="flex items-center gap-1 rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                    onClick={() => {
                      setDeleteError(null);
                      setConfirmDelete(h);
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

      {showAdd && (
        <AddHostForm
          orgId={selectedOrgId}
          onClose={() => setShowAdd(false)}
          onDone={async (host) => {
            setShowAdd(false);
            await load();
            setInstallHost(host);
          }}
        />
      )}

      {editHost && (
        <EditHostForm
          orgId={selectedOrgId}
          host={editHost}
          onClose={() => setEditHost(null)}
          onDone={async () => {
            setEditHost(null);
            setToast("Host updated.");
            await load();
          }}
        />
      )}

      {installHost && <InstallCommandModal orgId={selectedOrgId} host={installHost} onClose={() => setInstallHost(null)} />}

      <ConfirmDialog
        open={!!confirmDelete}
        title="Remove host"
        message={
          <>
            {`Remove "${confirmDelete?.name}" from Remote Management? This only removes DeployCore's own record - the agent keeps running on the machine itself and isn't uninstalled.`}
            {deleteError && <div className="mt-2 text-red-600 dark:text-red-400">{deleteError}</div>}
          </>
        }
        confirmLabel="Remove"
        onConfirm={deleteHost}
        onCancel={() => {
          setDeleteError(null);
          setConfirmDelete(null);
        }}
      />
    </div>
  );
}

function RemoteSetupBanner({ status }: { status: RemoteStatus }) {
  const ready = status.configured && status.reachable;

  return (
    <div
      className={
        ready
          ? "rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900"
          : "rounded-lg border border-amber-300 bg-amber-50 p-5 dark:border-amber-900/60 dark:bg-amber-950/40"
      }
    >
      {!ready && (
        <>
          <div className="mb-1 flex items-center gap-2">
            <AlertTriangle size={16} strokeWidth={1.75} className="text-amber-600 dark:text-amber-400" />
            <h2 className="text-sm font-semibold text-amber-900 dark:text-amber-300">Finish setting up Remote Management</h2>
          </div>
          <p className="mb-3 text-xs text-amber-800 dark:text-amber-400">
            {!status.configured
              ? "The Remote Management server isn't configured yet. If you installed with scripts/setup.sh this is normally automatic - re-run it, or set RUSTDESK_ADMIN_PASSWORD in .env and run the reset command below."
              : status.detail || "The Remote Management server isn't reachable yet."}
          </p>
          <pre className="mb-3 overflow-x-auto rounded-md border border-amber-200 bg-white px-3 py-2 text-xs dark:border-amber-900/60 dark:bg-neutral-900">
{`# From the DeployCore install directory on the host:
docker compose up -d rustdesk
docker compose exec -w /app rustdesk ./apimain reset-admin-pwd "$(grep '^RUSTDESK_ADMIN_PASSWORD=' .env | cut -d= -f2-)"`}
          </pre>
        </>
      )}

      {/* Port-forwarding guidance is always relevant: agents can be anywhere on
          the internet, and opening these ports to this host is the one setup
          step that genuinely can't be automated from inside a container. Kept
          in a native <details> so it's out of the way once you know it. */}
      <details className={ready ? "group p-4" : ""} open={!ready}>
        <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium">
          {ready ? (
            <CheckCircle2 size={16} strokeWidth={1.75} className="text-emerald-600" />
          ) : (
            <span className="text-amber-900 dark:text-amber-300" />
          )}
          <span className={ready ? "" : "text-amber-900 dark:text-amber-300"}>
            {ready ? "Remote Management is ready" : "Network & firewall setup"}
            <span className="ml-2 font-normal text-neutral-500">
              — to reach hosts outside this network, forward these ports to {status.relay_host}
            </span>
          </span>
        </summary>
        <div className="mt-3 space-y-3 text-xs">
          <p className="text-neutral-600 dark:text-neutral-400">
            Agents connect out to <code className="rounded bg-neutral-100 px-1 dark:bg-neutral-800">{status.relay_host}</code>.
            On the same LAN this already works. For agents on other networks/the internet, forward (and allow through any
            firewall) these ports to this host:
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="text-neutral-500">
                <tr>
                  <th className="py-1 pr-4 font-medium">Port</th>
                  <th className="py-1 pr-4 font-medium">Protocol</th>
                  <th className="py-1 font-medium">Purpose</th>
                </tr>
              </thead>
              <tbody className="font-mono">
                {status.ports.map((p) => (
                  <tr key={`${p.port}-${p.proto}`} className="border-t border-neutral-100 dark:border-neutral-800">
                    <td className="py-1 pr-4">{p.port}</td>
                    <td className="py-1 pr-4">{p.proto}</td>
                    <td className="py-1 font-sans text-neutral-600 dark:text-neutral-400">{p.purpose}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-neutral-500">
            Using a domain name instead of an IP? Point it at this host and set RUSTDESK_RELAY_HOST /
            RUSTDESK_API_PUBLIC_URL in .env to it.{" "}
            <Link to="/wiki" className="text-blue-600 hover:underline dark:text-blue-400">
              Full Remote Management guide
            </Link>
          </p>
        </div>
      </details>
    </div>
  );
}

function AddHostForm({
  orgId,
  onClose,
  onDone,
}: {
  orgId: string;
  onClose: () => void;
  onDone: (host: ManagedHost) => void;
}) {
  const [name, setName] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name) return;
    setError(null);
    setSaving(true);
    try {
      const host = await api.post<ManagedHost>(`/organizations/${orgId}/managed-hosts`, { name });
      onDone(host);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to add host.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5 shadow-sm">
        <h2 className="mb-1 text-sm font-semibold">Add host</h2>
        <p className="mb-4 text-xs text-neutral-500">
          For a server or workstation not deployed by DeployCore. You'll get an install command to run on it next.
        </p>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Name</label>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Front Desk Workstation"
          autoFocus
        />
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50" disabled={saving || !name}>
            {saving ? "Adding..." : "Add host"}
          </button>
        </div>
      </form>
    </div>
  );
}

function EditHostForm({
  orgId,
  host,
  onClose,
  onDone,
}: {
  orgId: string;
  host: ManagedHost;
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState(host.name);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name) return;
    setError(null);
    setSaving(true);
    try {
      await api.patch(`/organizations/${orgId}/managed-hosts/${host.id}`, { name });
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
        <h2 className="mb-4 text-sm font-semibold">Rename host</h2>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
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

function CopyableCommand({ command }: { command: string }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    await navigator.clipboard.writeText(command);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }
  return (
    <div className="flex items-center gap-2">
      <code className="flex-1 overflow-x-auto whitespace-nowrap rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1.5 text-xs dark:border-neutral-700 dark:bg-neutral-800">
        {command}
      </code>
      <button
        className="shrink-0 rounded-md border border-neutral-300 dark:border-neutral-700 p-1.5 hover:bg-neutral-50 dark:hover:bg-neutral-800"
        onClick={copy}
        title={copied ? "Copied" : "Copy"}
      >
        <Copy size={14} strokeWidth={1.75} className={copied ? "text-emerald-600" : ""} />
      </button>
    </div>
  );
}

function InstallCommandModal({ orgId, host, onClose }: { orgId: string; host: ManagedHost; onClose: () => void }) {
  const downloadUrl = `/api/organizations/${orgId}/managed-hosts/agent-installer`;
  const origin = window.location.origin;
  // Easiest path: one line in an elevated PowerShell, no file to download - the
  // script self-configures from this instance (server key, relay address) using
  // the enroll token. Mirrors Tailscale/RMM onboarding.
  const oneLiner = `powershell -ExecutionPolicy Bypass -Command "$env:DC_TOKEN='${host.enroll_token}'; irm ${origin}/api/remote/install-script | iex"`;
  // File path: the same silent install the deployment pipeline uses.
  const msiCommand = `msiexec /i DeployCoreRemoteAgent.msi /qn SERVERURL="${origin}" ENROLLTOKEN=${host.enroll_token}`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="w-[34rem] rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5 shadow-sm">
        <h2 className="mb-1 text-sm font-semibold">Install the Remote Management Agent</h2>
        <p className="mb-4 text-xs text-neutral-500">
          Run this on <strong>{host.name}</strong>. It installs silently in the background - the host shows as enrolled
          here within a minute.
        </p>

        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">
          Easiest — paste into an <strong>Administrator</strong> PowerShell
        </label>
        <div className="mb-4">
          <CopyableCommand command={oneLiner} />
        </div>

        <div className="mb-2 flex items-center gap-2 text-xs text-neutral-400">
          <span className="h-px flex-1 bg-neutral-200 dark:bg-neutral-800" />
          or install from the .msi
          <span className="h-px flex-1 bg-neutral-200 dark:bg-neutral-800" />
        </div>

        <a
          href={downloadUrl}
          className="mb-2 flex w-full items-center justify-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
        >
          <Download size={14} strokeWidth={1.75} />
          Download DeployCoreRemoteAgent.msi
        </a>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">
          Then run (as Administrator, from the download folder)
        </label>
        <CopyableCommand command={msiCommand} />

        <div className="mt-5 flex justify-end">
          <button className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </div>
  );
}
