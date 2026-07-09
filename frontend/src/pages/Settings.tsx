import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, getToken } from "../api/client";
import ConfirmDialog from "../components/ConfirmDialog";
import FileDropzone from "../components/FileDropzone";
import { useAuth, roleAtLeast } from "../state/auth";
import { useInstanceInfo } from "../state/instance";
import { useOrg } from "../state/org";

interface SettingRow {
  scope: string;
  key: string;
  value: unknown;
}

export default function SettingsPage() {
  const { user } = useAuth();
  const isGlobalAdmin = !!user && roleAtLeast(user.global_role, "admin");

  return (
    <div className="space-y-8">
      <h1 className="text-lg font-semibold">Settings</h1>
      {isGlobalAdmin && (
        <div className="columns-1 gap-4 xl:columns-2 [&>*]:mb-4 [&>*]:break-inside-avoid">
          <UpdatesPanel />
          <MspOrganizationPanel />
          <TlsPanel />
          <BackupsPanel />
          <M365Panel />
        </div>
      )}
      <OrgSettingsPanel />
    </div>
  );
}

function MspOrganizationPanel() {
  const { name: currentName, hasLogo } = useInstanceInfo();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [logoExists, setLogoExists] = useState(hasLogo);
  const [logoError, setLogoError] = useState<string | null>(null);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [confirmRemoveLogo, setConfirmRemoveLogo] = useState(false);

  useEffect(() => setName(currentName), [currentName]);
  useEffect(() => setLogoExists(hasLogo), [hasLogo]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    if (!name) {
      setError("Instance name is required.");
      return;
    }
    try {
      await api.put("/settings/global/instance_name", { value: name });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to rename instance.");
    }
  }

  async function uploadLogo(file: File | null) {
    if (!file) return;
    setLogoError(null);
    setUploadingLogo(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch("/api/settings/global/logo", {
        method: "PUT",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: formData,
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Upload failed.");
      setLogoExists(true);
    } catch (err) {
      setLogoError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploadingLogo(false);
    }
  }

  async function removeLogo() {
    await api.delete("/settings/global/logo");
    setLogoExists(false);
    setConfirmRemoveLogo(false);
  }

  return (
    <div className="space-y-4">
      <form noValidate onSubmit={onSubmit} className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-1 text-sm font-semibold">MSP Organization</h2>
        <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
          Your own organization's name, set once during initial setup, shown in the sidebar and sign-in screen.
        </p>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Name</label>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        {saved && <div className="mb-3 text-xs text-emerald-600">Saved.</div>}
        <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
          Apply changes
        </button>
      </form>

      <div className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-1 text-sm font-semibold">Logo</h2>
        <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
          Shown alongside the instance name in the sidebar and sign-in screen. PNG, JPEG, or SVG, under 5 MB.
          Transparent backgrounds are preserved, so a PNG or SVG cut to just the mark looks best.
        </p>
        {logoExists && (
          <div className="mb-3 flex items-center justify-between rounded-md border border-neutral-200 bg-[repeating-conic-gradient(#f5f5f5_0%_25%,white_0%_50%)] bg-[length:16px_16px] p-3 dark:border-neutral-700 dark:bg-[repeating-conic-gradient(#262626_0%_25%,#171717_0%_50%)]">
            <img src="/api/instance/logo" alt="Current logo" className="max-h-16 max-w-[12rem] object-contain" />
            <button className="text-xs text-red-600 hover:underline dark:text-red-400" onClick={() => setConfirmRemoveLogo(true)}>
              Remove
            </button>
          </div>
        )}
        <FileDropzone
          accept=".png,.jpg,.jpeg,.svg"
          hint={uploadingLogo ? "Uploading..." : "PNG, JPEG, or SVG"}
          onSelect={uploadLogo}
        />
        {logoError && <div className="mt-3 text-xs text-red-600">{logoError}</div>}
      </div>

      <ConfirmDialog
        open={confirmRemoveLogo}
        title="Remove logo"
        message="Removes the logo from the sidebar and sign-in screen. The instance name still shows on its own."
        confirmLabel="Remove"
        onConfirm={removeLogo}
        onCancel={() => setConfirmRemoveLogo(false)}
      />
    </div>
  );
}

interface TlsStatus {
  mode: "self_signed" | "uploaded";
  has_uploaded_certificate: boolean;
  uploaded_subject: string | null;
  uploaded_expires_at: string | null;
}

function TlsPanel() {
  const [status, setStatus] = useState<TlsStatus | null>(null);
  const [certFile, setCertFile] = useState<File | null>(null);
  const [keyFile, setKeyFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setStatus(await api.get<TlsStatus>("/settings/global/tls"));
  }

  useEffect(() => {
    load();
  }, []);

  async function upload() {
    if (!certFile || !keyFile) return;
    setError(null);
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("cert_file", certFile);
      formData.append("key_file", keyFile);
      const res = await fetch("/api/settings/global/tls/certificate", {
        method: "PUT",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: formData,
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Upload failed.");
      setCertFile(null);
      setKeyFile(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function setMode(mode: "self_signed" | "uploaded") {
    setError(null);
    setSwitching(true);
    try {
      await api.put("/settings/global/tls/mode", { value: mode });
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to switch modes.");
    } finally {
      setSwitching(false);
    }
  }

  if (!status) return null;

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
      <h2 className="mb-1 text-sm font-semibold">HTTPS certificate</h2>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        DeployCore is served over HTTPS by a built-in reverse proxy, HTTP requests are redirected
        automatically. By default it uses a self-signed certificate it generates itself, so browsers
        will show a warning until you upload one signed by a certificate authority they trust.
      </p>

      <div className="mb-3 flex items-center gap-2 text-xs">
        <span className="text-neutral-500 dark:text-neutral-400">Currently serving:</span>
        <span
          className={`rounded-full px-2 py-0.5 font-medium ${
            status.mode === "uploaded"
              ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
              : "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-400"
          }`}
        >
          {status.mode === "uploaded" ? "Uploaded certificate" : "Self-signed certificate"}
        </span>
      </div>

      {status.has_uploaded_certificate && (
        <div className="mb-3 rounded-md border border-neutral-200 bg-neutral-50 p-3 text-xs dark:border-neutral-700 dark:bg-neutral-800/50">
          <div className="text-neutral-700 dark:text-neutral-300">{status.uploaded_subject}</div>
          <div className="text-neutral-500 dark:text-neutral-400">
            Expires {status.uploaded_expires_at ? new Date(status.uploaded_expires_at).toLocaleDateString() : "unknown"}
          </div>
          <button
            disabled={switching}
            className="mt-2 text-xs text-blue-600 hover:underline disabled:opacity-50 dark:text-blue-400"
            onClick={() => setMode(status.mode === "uploaded" ? "self_signed" : "uploaded")}
          >
            {status.mode === "uploaded" ? "Switch to self-signed temporarily" : "Use this certificate again"}
          </button>
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <FileDropzone accept=".pem,.crt,.cer" fileName={certFile?.name} hint="Certificate (PEM)" onSelect={setCertFile} />
        <FileDropzone accept=".pem,.key" fileName={keyFile?.name} hint="Private key (PEM)" onSelect={setKeyFile} />
      </div>
      {error && <div className="mt-3 text-xs text-red-600">{error}</div>}
      <button
        disabled={!certFile || !keyFile || uploading}
        className="mt-3 rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        onClick={upload}
      >
        {uploading ? "Uploading..." : "Upload certificate"}
      </button>
    </div>
  );
}

interface UpdateStatus {
  git_available: boolean;
  current_commit: string | null;
  latest_commit: string | null;
  commits_behind: number;
  checked_at: string | null;
  stage: string;
  error: string | null;
}

const IN_PROGRESS_STAGES = new Set(["pulling", "building", "restarting", "finalizing"]);
const STAGE_LABELS: Record<string, string> = {
  pulling: "Pulling latest code...",
  building: "Rebuilding images...",
  restarting: "Restarting services...",
  finalizing: "Waiting for the app to come back...",
  done: "Up to date",
  failed: "Update failed",
  disabled: "Self-update unavailable",
};

function UpdatesPanel() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [confirmUpdate, setConfirmUpdate] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);

  async function load() {
    try {
      setStatus(await api.get<UpdateStatus>("/settings/global/update/status"));
    } catch {
      // transient: the api container may be mid-restart during an update
    }
  }

  useEffect(() => {
    load();
    const inProgress = status ? IN_PROGRESS_STAGES.has(status.stage) : false;
    const interval = setInterval(load, inProgress ? 2000 : 60000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.stage]);

  async function triggerUpdate() {
    setConfirmUpdate(false);
    setTriggerError(null);
    try {
      await api.post("/settings/global/update/run");
      await load();
    } catch (err) {
      setTriggerError(err instanceof ApiError ? err.message : "Failed to start the update.");
    }
  }

  if (!status) return null;

  const inProgress = IN_PROGRESS_STAGES.has(status.stage);
  const upToDate = status.commits_behind === 0;

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
      <h2 className="mb-1 text-sm font-semibold">Updates</h2>
      {!status.git_available ? (
        <div className="text-xs text-neutral-500 dark:text-neutral-400">
          <p className="mb-2">
            Self-update is unavailable. Update manually:{" "}
            <code>git pull && docker compose up -d --build</code>.
          </p>
          {status.error && (
            <p className="rounded-md border border-amber-200 bg-amber-50 p-2 font-mono text-[0.7rem] text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-400">
              {status.error}
            </p>
          )}
        </div>
      ) : (
        <>
          <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
            Current version: <span className="font-mono">{status.current_commit ?? "unknown"}</span>
            {upToDate ? (
              <span className="ml-2 text-emerald-600 dark:text-emerald-400">up to date</span>
            ) : (
              <span className="ml-2 text-amber-600 dark:text-amber-400">{status.commits_behind} commit(s) behind</span>
            )}
          </p>
          {inProgress && (
            <div className="mb-3 rounded-md border border-blue-200 bg-blue-50 p-2 text-xs text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-400">
              {STAGE_LABELS[status.stage] ?? status.stage}
              {" "}(the app will be briefly unreachable while it restarts)
            </div>
          )}
          {status.stage === "failed" && status.error && (
            <div className="mb-3 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
              Last update failed: {status.error}
            </div>
          )}
          {triggerError && <div className="mb-3 text-xs text-red-600">{triggerError}</div>}
          <button
            disabled={inProgress}
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            onClick={() => setConfirmUpdate(true)}
          >
            {inProgress ? "Updating..." : "Update now"}
          </button>
        </>
      )}

      <ConfirmDialog
        open={confirmUpdate}
        title="Update DeployCore"
        message="Pulls the latest code, rebuilds, and restarts the app. It will be briefly unreachable (usually under a minute). Existing data is not affected."
        confirmLabel="Update now"
        onConfirm={triggerUpdate}
        onCancel={() => setConfirmUpdate(false)}
      />
    </div>
  );
}

interface BackupFile {
  filename: string;
  size_bytes: number;
  created_at: number;
}

function BackupsPanel() {
  const [backups, setBackups] = useState<BackupFile[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setBackups(await api.get<BackupFile[]>("/settings/global/backups"));
  }

  useEffect(() => {
    load();
  }, []);

  async function runNow() {
    setRunning(true);
    setError(null);
    try {
      await api.post("/settings/global/backups/run");
      await new Promise((r) => setTimeout(r, 2000));
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start backup.");
    } finally {
      setRunning(false);
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  async function downloadBackup(filename: string) {
    const res = await fetch(`/api/settings/global/backups/${filename}`, {
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold">Database backups</h2>
          <p className="text-xs text-neutral-500 dark:text-neutral-400">Daily automatic backup, plus manual runs. Newest 14 kept.</p>
        </div>
        <button
          disabled={running}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
          onClick={runNow}
        >
          {running ? "Running..." : "Run backup now"}
        </button>
      </div>
      {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
      <div className="divide-y divide-neutral-100 rounded-md border border-neutral-200 text-sm dark:divide-neutral-800 dark:border-neutral-700">
        {backups.length === 0 && <div className="p-3 text-xs text-neutral-400">No backups yet.</div>}
        {backups.map((b) => (
          <div key={b.filename} className="flex items-center justify-between px-3 py-2">
            <div>
              <div className="text-xs">{b.filename}</div>
              <div className="text-xs text-neutral-400">
                {new Date(b.created_at * 1000).toLocaleString()} · {formatSize(b.size_bytes)}
              </div>
            </div>
            <button className="text-xs text-blue-600 hover:underline dark:text-blue-400" onClick={() => downloadBackup(b.filename)}>
              Download
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

interface M365Config {
  tenant_id: string;
  client_id: string;
  sender_upn: string;
  enabled: boolean;
  configured: boolean;
}

function M365Panel() {
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [senderUpn, setSenderUpn] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);

  async function load() {
    const config = await api.get<M365Config>("/settings/global/m365");
    setTenantId(config.tenant_id);
    setClientId(config.client_id);
    setSenderUpn(config.sender_upn);
    setEnabled(config.enabled);
    setConfigured(config.configured);
  }

  useEffect(() => {
    load();
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      await api.put("/settings/global/m365", {
        tenant_id: tenantId,
        client_id: clientId,
        client_secret: clientSecret || null,
        sender_upn: senderUpn,
        enabled,
      });
      setClientSecret("");
      setSaved(true);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save M365 configuration.");
    }
  }

  async function sendTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.post<{ ok: boolean; message: string }>("/settings/global/m365/test");
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof ApiError ? err.message : "Test failed." });
    } finally {
      setTesting(false);
    }
  }

  async function onSubmitChecked(e: FormEvent) {
    if (!tenantId || !clientId || !senderUpn || (!configured && !clientSecret)) {
      e.preventDefault();
      setError("Tenant ID, client ID, sender mailbox, and a client secret are required.");
      return;
    }
    await onSubmit(e);
  }

  return (
    <form noValidate onSubmit={onSubmitChecked} className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
      <h2 className="mb-1 text-sm font-semibold">Email notifications (Microsoft 365)</h2>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        Sends deployment notifications by email via Microsoft Graph, using an app-only Azure AD app
        registration (Mail.Send application permission). Instance-wide, not per-organization.
      </p>
      <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Tenant ID</label>
      <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={tenantId} onChange={(e) => setTenantId(e.target.value)} />
      <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Client ID</label>
      <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={clientId} onChange={(e) => setClientId(e.target.value)} />
      <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">
        Client secret {configured && <span className="text-neutral-400">(leave blank to keep the current one)</span>}
      </label>
      <input
        type="password"
        className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
        value={clientSecret}
        onChange={(e) => setClientSecret(e.target.value)}
      />
      <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Sender mailbox (UPN)</label>
      <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={senderUpn} onChange={(e) => setSenderUpn(e.target.value)} />
      <label className="mb-3 flex items-center gap-2 text-xs font-medium text-neutral-600 dark:text-neutral-400">
        <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        Enabled
      </label>
      {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
      {saved && <div className="mb-3 text-xs text-emerald-600">Saved.</div>}
      <div className="flex items-center gap-2">
        <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
          Apply changes
        </button>
        {configured && (
          <button
            type="button"
            disabled={testing}
            className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50"
            onClick={sendTest}
          >
            {testing ? "Sending..." : "Send test email"}
          </button>
        )}
      </div>
      {testResult && (
        <div className={`mt-3 rounded-md border p-2 text-xs ${testResult.ok ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-400" : "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400"}`}>
          {testResult.message}
        </div>
      )}
    </form>
  );
}

const DEFAULT_TIMEOUT_MINUTES = 90;

function OrgSettingsPanel() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [timeoutMinutes, setTimeoutMinutes] = useState(DEFAULT_TIMEOUT_MINUTES);
  const [timeoutSaved, setTimeoutSaved] = useState(false);
  const [timeoutError, setTimeoutError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [key, setKey] = useState("");
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);

  const canManage = roleAtLeast(effectiveRole(selectedOrgId), "admin");

  async function load() {
    if (!selectedOrgId) return;
    const data = await api.get<SettingRow[]>(`/organizations/${selectedOrgId}/settings`);
    setRows(data);
    const timeoutRow = data.find((r) => r.key === "os_install_timeout_minutes");
    if (timeoutRow && typeof timeoutRow.value === "number") setTimeoutMinutes(timeoutRow.value);
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  if (!selectedOrgId) return null;

  async function saveTimeout(e: FormEvent) {
    e.preventDefault();
    setTimeoutError(null);
    setTimeoutSaved(false);
    try {
      await api.put(`/organizations/${selectedOrgId}/settings/os_install_timeout_minutes`, { value: timeoutMinutes });
      setTimeoutSaved(true);
      await load();
    } catch (err) {
      setTimeoutError(err instanceof ApiError ? err.message : "Failed to save.");
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      let parsed: unknown = value;
      try {
        parsed = JSON.parse(value);
      } catch {
        // not JSON, keep as raw string
      }
      await api.put(`/organizations/${selectedOrgId}/settings/${key}`, { value: parsed });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save setting.");
      return;
    }
    await load();
  }

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-sm font-semibold text-neutral-700 dark:text-neutral-300">Deployment settings</h2>
        <p className="text-sm text-neutral-500 dark:text-neutral-400">Applies to this organization only.</p>
      </div>

      <form onSubmit={saveTimeout} className="max-w-md rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Deployment timeout (minutes)</label>
        <p className="mb-2 text-xs text-neutral-500 dark:text-neutral-400">
          A deployment stuck past this many minutes in any non-terminal stage is force-failed automatically.
        </p>
        <input
          type="number"
          min={1}
          disabled={!canManage}
          className="mb-3 w-32 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm disabled:bg-neutral-50 dark:bg-neutral-900 dark:disabled:bg-neutral-800"
          value={timeoutMinutes}
          onChange={(e) => setTimeoutMinutes(Number(e.target.value))}
        />
        {timeoutError && <div className="mb-3 text-xs text-red-600">{timeoutError}</div>}
        {timeoutSaved && <div className="mb-3 text-xs text-emerald-600">Saved.</div>}
        {canManage && (
          <button type="submit" className="block rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
            Apply changes
          </button>
        )}
      </form>

      <button
        className="text-xs text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
        onClick={() => setShowAdvanced(!showAdvanced)}
      >
        {showAdvanced ? "Hide advanced" : "Advanced: view/set raw settings keys"}
      </button>

      {showAdvanced && (
        <div className="space-y-4">
          <div className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 bg-white text-sm dark:divide-neutral-800 dark:border-neutral-700 dark:bg-neutral-900">
            {rows.length === 0 && <div className="p-4 text-neutral-400">No settings overrides configured.</div>}
            {rows.map((r) => (
              <div key={`${r.scope}-${r.key}`} className="flex items-center justify-between px-4 py-2.5">
                <span className="font-medium">{r.key}</span>
                <span className="text-xs text-neutral-400">{r.scope}</span>
                <span>{JSON.stringify(r.value)}</span>
              </div>
            ))}
          </div>

          {canManage && (
            <form noValidate onSubmit={onSubmit} className="max-w-md rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
              <h2 className="mb-3 text-sm font-semibold">Set organization override</h2>
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Key</label>
              <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={key} onChange={(e) => setKey(e.target.value)} />
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Value (JSON or plain text)</label>
              <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={value} onChange={(e) => setValue(e.target.value)} />
              {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
              <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">Save</button>
            </form>
          )}
        </div>
      )}
    </div>
  );
}
