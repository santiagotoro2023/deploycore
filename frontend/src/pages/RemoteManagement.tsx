import { AlertTriangle, CheckCircle2, Copy, Download, KeySquare, MonitorSmartphone, Pencil, Plus, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, ApiError, getToken } from "../api/client";
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
                  title={h.enrolled ? "View and control the current screen, as-is" : "Waiting for the agent to enroll"}
                  onClick={() => navigate(`/remote-management/${h.id}`)}
                >
                  <MonitorSmartphone size={12} strokeWidth={1.75} />
                  Shadow
                </button>
                <button
                  className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 disabled:cursor-not-allowed disabled:opacity-40 dark:hover:bg-neutral-800"
                  disabled={!h.enrolled}
                  title={
                    h.enrolled
                      ? "Open the same screen view, then try auto-typing this host's saved RDP username/password"
                      : "Waiting for the agent to enroll"
                  }
                  onClick={() => navigate(`/remote-management/${h.id}?mode=connect`)}
                >
                  <KeySquare size={12} strokeWidth={1.75} />
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

      {installHost && (
        <InstallCommandModal orgId={selectedOrgId} host={installHost} appPublicUrl={status?.app_public_url} onClose={() => setInstallHost(null)} />
      )}

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
            To reach agents from outside this network, set your public IP or domain in{" "}
            <Link to="/settings" className="text-blue-600 hover:underline dark:text-blue-400">
              Settings → Remote Management
            </Link>{" "}
            and forward the ports above.{" "}
            <Link to="/wiki" className="text-blue-600 hover:underline dark:text-blue-400">
              Full guide
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
  const [rdpUsername, setRdpUsername] = useState(host.rdp_username ?? "");
  const [rdpPassword, setRdpPassword] = useState("");
  const [clearRdpPassword, setClearRdpPassword] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!name) return;
    setError(null);
    setSaving(true);
    try {
      const body: Record<string, string> = { name, rdp_username: rdpUsername };
      // Only include rdp_password at all if the operator actually typed a
      // new one or explicitly asked to clear it - the current value is
      // never sent to this form to begin with (see ManagedHostRead's own
      // rdp_password_set field), so an omitted key here means "leave it
      // as whatever's already stored", not "no password".
      if (clearRdpPassword) {
        body.rdp_password = "";
      } else if (rdpPassword) {
        body.rdp_password = rdpPassword;
      }
      await api.patch(`/organizations/${orgId}/managed-hosts/${host.id}`, body);
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
        <h2 className="mb-4 text-sm font-semibold">Edit host</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Name</label>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />

        <div className="mb-3 border-t border-neutral-200 pt-3 dark:border-neutral-800">
          <p className="mb-2 text-xs text-neutral-500">
            Used by "Connect" to try auto-typing into the remote login screen - best-effort, not guaranteed to work
            on every session.
          </p>
          <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">RDP username</label>
          <input
            className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
            value={rdpUsername}
            onChange={(e) => setRdpUsername(e.target.value)}
            placeholder="e.g. Administrator"
          />
          <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">RDP password</label>
          <input
            type="password"
            className="mb-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900 disabled:opacity-50"
            value={rdpPassword}
            onChange={(e) => setRdpPassword(e.target.value)}
            disabled={clearRdpPassword}
            placeholder={host.rdp_password_set ? "Leave blank to keep the current password" : "No password set"}
          />
          <label className="mb-3 flex items-center gap-1.5 text-xs text-neutral-500">
            <input
              type="checkbox"
              checked={clearRdpPassword}
              onChange={(e) => {
                setClearRdpPassword(e.target.checked);
                if (e.target.checked) setRdpPassword("");
              }}
            />
            Clear the stored password
          </label>
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

// PowerShell's -EncodedCommand expects base64 of the script as UTF-16LE -
// used instead of a plain -Command "..." string so this is immune to the
// calling shell's own quoting/interpolation rules. Confirmed live this
// matters: a double-quoted -Command "$env:DC_TOKEN='...'; ..." gets its
// $env:DC_TOKEN assignment silently expanded by the OUTER shell (evaluating
// to empty, since DC_TOKEN isn't set yet there) when pasted into an
// already-open PowerShell window rather than cmd.exe/Run - the only context
// that string was ever actually safe in - producing a mangled token and a
// silent enrollment failure with no obvious cause.
function toPowerShellEncodedCommand(script: string): string {
  const bytes = new Uint8Array(script.length * 2);
  for (let i = 0; i < script.length; i++) {
    const code = script.charCodeAt(i);
    bytes[i * 2] = code & 0xff;
    bytes[i * 2 + 1] = (code >> 8) & 0xff;
  }
  let binary = "";
  bytes.forEach((b) => (binary += String.fromCharCode(b)));
  return btoa(binary);
}

function InstallCommandModal({
  orgId,
  host,
  appPublicUrl,
  onClose,
}: {
  orgId: string;
  host: ManagedHost;
  appPublicUrl?: string;
  onClose: () => void;
}) {
  const downloadUrl = `/api/organizations/${orgId}/managed-hosts/agent-installer`;
  // Plain <a href> can't carry the Authorization header this route requires
  // (token-based auth, no session cookie) - the browser's own top-level
  // navigation sends no Authorization at all, so a bare link always 401s.
  // Fetch with the token instead and hand the browser a blob URL, same
  // pattern as Settings.tsx's downloadBackup().
  async function downloadMsi() {
    const res = await fetch(downloadUrl, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "DeployCoreRemoteAgent.msi";
    link.click();
    URL.revokeObjectURL(url);
  }
  // The SAME address the automated deployment pipeline uses for a freshly
  // provisioned VM (provision.py's own SERVERURL, from config.app_public_url)
  // - not window.location.origin, which is just whatever address the
  // operator's own browser happens to be pointed at right now (a port
  // forward, VPN, etc.) and is not guaranteed reachable from the target
  // machine's own network. Confirmed live as a real cause of enrollment
  // failures when the two diverged. Falls back to the browser's own origin
  // only if /api/remote/status hasn't loaded yet.
  const serverUrl = appPublicUrl || window.location.origin;
  const script = `$env:DC_TOKEN='${host.enroll_token}'; irm ${serverUrl}/api/remote/install-script | iex`;
  // Easiest path: one line in an elevated PowerShell, no file to download - the
  // script self-configures from this instance (server key, relay address) using
  // the enroll token. Mirrors Tailscale/RMM onboarding.
  const oneLiner = `powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand ${toPowerShellEncodedCommand(script)}`;
  // File path: the same silent install the deployment pipeline uses.
  const msiCommand = `msiexec /i DeployCoreRemoteAgent.msi /qn SERVERURL="${serverUrl}" ENROLLTOKEN=${host.enroll_token}`;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="w-[34rem] rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5 shadow-sm">
        <h2 className="mb-1 text-sm font-semibold">Install the Remote Management Agent</h2>
        <p className="mb-4 text-xs text-neutral-500">
          Run this on <strong>{host.name}</strong>. It installs silently in the background - the host shows as enrolled
          here within a minute. Will register with this instance at{" "}
          <code className="rounded bg-neutral-100 px-1 dark:bg-neutral-800">{serverUrl}</code> — make sure that address
          is reachable from {host.name} (Settings → Remote Management if it needs changing).
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

        <button
          type="button"
          onClick={downloadMsi}
          className="mb-2 flex w-full items-center justify-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
        >
          <Download size={14} strokeWidth={1.75} />
          Download DeployCoreRemoteAgent.msi
        </button>
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
