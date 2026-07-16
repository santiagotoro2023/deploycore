import { FormEvent, ReactNode, useEffect, useRef, useState } from "react";
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

interface SettingsCardDef {
  id: string;
  title: string;
  description: string;
}

const INSTANCE_SETTINGS: SettingsCardDef[] = [
  { id: "msp", title: "MSP Organization", description: "Instance name and logo, shown in the sidebar and sign-in screen." },
  { id: "updates", title: "Updates", description: "Check for and apply DeployCore updates." },
  { id: "remote-management", title: "Remote Management", description: "The address agents and browsers use to reach this instance — set a public URL for internet access." },
  { id: "backups", title: "Database backups", description: "Manual and scheduled backups of the whole database." },
  { id: "m365", title: "Email notifications", description: "Deployment emails sent through your Microsoft 365 tenant." },
  { id: "teams", title: "Teams notifications", description: "Deployment notifications sent directly to users in Teams." },
  { id: "notification-content", title: "Notification content", description: "Customize the subject/body/message sent for each event." },
  { id: "tls", title: "HTTPS certificate", description: "Self-signed by default, or upload your own trusted certificate." },
];

function SettingsCard({ title, description, onOpen }: { title: string; description: string; onOpen: () => void }) {
  return (
    <div className="flex flex-col justify-between rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-700 dark:bg-neutral-900">
      <div>
        <h3 className="mb-1 text-sm font-semibold">{title}</h3>
        <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">{description}</p>
      </div>
      <button
        type="button"
        className="self-start rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
        onClick={onOpen}
      >
        Configure
      </button>
    </div>
  );
}

function SettingsModal({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/30 py-10">
      <div className="w-full max-w-lg space-y-4 px-4">
        {children}
        <button
          type="button"
          className="w-full rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-900 dark:hover:bg-neutral-800"
          onClick={onClose}
        >
          Close
        </button>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  const { user } = useAuth();
  const { selectedOrgId } = useOrg();
  const isGlobalAdmin = !!user && roleAtLeast(user.global_role, "admin");
  const [activeModal, setActiveModal] = useState<string | null>(null);

  return (
    <div className="space-y-8">
      <h1 className="text-lg font-semibold">Settings</h1>
      {isGlobalAdmin && (
        <div>
          <h2 className="mb-3 text-sm font-semibold text-neutral-700 dark:text-neutral-300">Instance settings</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {INSTANCE_SETTINGS.map((item) => (
              <SettingsCard key={item.id} title={item.title} description={item.description} onOpen={() => setActiveModal(item.id)} />
            ))}
          </div>
        </div>
      )}
      {selectedOrgId && (
        <div>
          <h2 className="mb-3 text-sm font-semibold text-neutral-700 dark:text-neutral-300">Organization settings</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <SettingsCard
              title="Deployment settings"
              description="Deployment timeout and advanced per-organization overrides."
              onOpen={() => setActiveModal("org")}
            />
          </div>
        </div>
      )}

      {activeModal && (
        <SettingsModal onClose={() => setActiveModal(null)}>
          {activeModal === "msp" && <MspOrganizationPanel />}
          {activeModal === "updates" && <UpdatesPanel />}
          {activeModal === "remote-management" && <RemoteManagementPanel />}
          {activeModal === "backups" && <BackupsPanel />}
          {activeModal === "m365" && <M365Panel />}
          {activeModal === "teams" && <TeamsPanel />}
          {activeModal === "notification-content" && <NotificationTemplatesPanel />}
          {activeModal === "tls" && <TlsPanel />}
          {activeModal === "org" && <OrgSettingsPanel />}
        </SettingsModal>
      )}
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
  pending_changelog: string[];
  last_update_changelog: string[];
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

const CHECK_TIMEOUT_MS = 20000;
const STAGE_ORDER = ["pulling", "building", "restarting", "finalizing", "done"];
function stagePercent(stage: string): number {
  const idx = STAGE_ORDER.indexOf(stage);
  return idx === -1 ? 5 : Math.round(((idx + 1) / STAGE_ORDER.length) * 100);
}

function WhatsNewList({ title, entries, collapsedByDefault = false }: { title: string; entries: string[]; collapsedByDefault?: boolean }) {
  const [open, setOpen] = useState(!collapsedByDefault);
  return (
    <div className="mb-3">
      <button
        type="button"
        className="text-xs text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100"
        onClick={() => setOpen(!open)}
      >
        {open ? "▾" : "▸"} {title}
      </button>
      {open && (
        <ul className="mt-1.5 list-disc space-y-0.5 pl-5 text-xs text-neutral-600 dark:text-neutral-400">
          {entries.map((entry, i) => (
            <li key={i}>{entry}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function UpdatesPanel() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [confirmUpdate, setConfirmUpdate] = useState(false);
  const [triggerError, setTriggerError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const checkBaselineRef = useRef<string | null>(null);
  const checkTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reloadedRef = useRef(false);

  async function load() {
    try {
      const next = await api.get<UpdateStatus>("/settings/global/update/status");
      setStatus(next);
      if (checking && next.checked_at && next.checked_at !== checkBaselineRef.current) {
        setChecking(false);
        if (checkTimeoutRef.current) clearTimeout(checkTimeoutRef.current);
      }
    } catch {
      // transient: the api container may be mid-restart during an update
    }
  }

  useEffect(() => {
    load();
    const inProgress = status ? IN_PROGRESS_STAGES.has(status.stage) : false;
    const interval = setInterval(load, inProgress || checking ? 2000 : 60000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.stage, checking]);

  async function triggerUpdate() {
    setConfirmUpdate(false);
    setTriggerError(null);
    try {
      await api.post("/settings/global/update/run");
      reloadedRef.current = false;
      setModalOpen(true);
      await load();
    } catch (err) {
      setTriggerError(err instanceof ApiError ? err.message : "Failed to start the update.");
    }
  }

  useEffect(() => {
    if (status && IN_PROGRESS_STAGES.has(status.stage)) setModalOpen(true);
  }, [status?.stage]);

  useEffect(() => {
    if (modalOpen && status?.stage === "done" && !reloadedRef.current) {
      reloadedRef.current = true;
      const t = setTimeout(() => window.location.reload(), 1200);
      return () => clearTimeout(t);
    }
  }, [modalOpen, status?.stage]);

  async function checkForUpdate() {
    setCheckError(null);
    checkBaselineRef.current = status?.checked_at ?? null;
    setChecking(true);
    try {
      await api.post("/settings/global/update/check");
      if (checkTimeoutRef.current) clearTimeout(checkTimeoutRef.current);
      checkTimeoutRef.current = setTimeout(() => setChecking(false), CHECK_TIMEOUT_MS);
    } catch (err) {
      setChecking(false);
      setCheckError(err instanceof ApiError ? err.message : "Failed to check for updates.");
    }
  }

  useEffect(() => {
    return () => {
      if (checkTimeoutRef.current) clearTimeout(checkTimeoutRef.current);
    };
  }, []);

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
          <p className="mb-1 text-xs text-neutral-500 dark:text-neutral-400">
            Current version: <span className="font-mono">{status.current_commit ?? "unknown"}</span>
            {upToDate ? (
              <span className="ml-2 text-emerald-600 dark:text-emerald-400">up to date</span>
            ) : (
              <span className="ml-2 text-amber-600 dark:text-amber-400">{status.commits_behind} commit(s) behind</span>
            )}
          </p>
          <p className="mb-3 text-xs text-neutral-400 dark:text-neutral-500">
            Last checked: {status.checked_at ? new Date(status.checked_at).toLocaleString() : "never"}
          </p>
          {!upToDate && status.pending_changelog.length > 0 && (
            <WhatsNewList title={`What's new (${status.pending_changelog.length})`} entries={status.pending_changelog} />
          )}
          {status.last_update_changelog.length > 0 && (
            <WhatsNewList title="What the last update changed" entries={status.last_update_changelog} collapsedByDefault />
          )}
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
          {checkError && <div className="mb-3 text-xs text-red-600">{checkError}</div>}
          <div className="flex items-center gap-2">
            <button
              disabled={inProgress || checking}
              className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50"
              onClick={checkForUpdate}
            >
              {checking ? "Checking..." : "Check for update"}
            </button>
            <button
              disabled={inProgress}
              className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
              onClick={() => setConfirmUpdate(true)}
            >
              {inProgress ? "Updating..." : "Update now"}
            </button>
          </div>
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

      {modalOpen && status && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
          <div className="w-96 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5 shadow-sm">
            <h2 className="mb-4 text-sm font-semibold">
              {status.stage === "failed" ? "Update failed" : status.stage === "done" ? "Update complete" : "Updating DeployCore"}
            </h2>
            <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
              <div
                className={`h-full transition-all ${status.stage === "failed" ? "bg-red-600" : "bg-blue-600"}`}
                style={{ width: `${stagePercent(status.stage)}%` }}
              />
            </div>
            <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
              {status.stage === "failed"
                ? status.error ?? "Update failed."
                : status.stage === "done"
                ? "Refreshing..."
                : `${STAGE_LABELS[status.stage] ?? "Working..."} The app will be briefly unreachable while it restarts.`}
            </p>
            {status.stage === "failed" && (
              <div className="flex justify-end">
                <button
                  type="button"
                  className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                  onClick={() => setModalOpen(false)}
                >
                  Close
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

interface BackupFile {
  filename: string;
  size_bytes: number;
  created_at: number;
}

interface RemoteManagementConfig {
  host: string;
  app_public_url: string;
  ports: { port: number; proto: string; purpose: string }[];
  apply_status: { stage: string; error: string | null } | null;
}

function RemoteManagementPanel() {
  const [config, setConfig] = useState<RemoteManagementConfig | null>(null);
  const [host, setHost] = useState("");
  const [appPublicUrl, setAppPublicUrl] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function load() {
    const c = await api.get<RemoteManagementConfig>("/settings/remote-management");
    setConfig(c);
    setHost(c.host);
    setAppPublicUrl(c.app_public_url);
  }

  useEffect(() => {
    load();
  }, []);

  // While an Apply is restarting the relay servers, poll so the status clears.
  useEffect(() => {
    if (config?.apply_status?.stage !== "applying") return;
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [config?.apply_status?.stage]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!host.trim()) return;
    setError(null);
    setSaving(true);
    setSaved(false);
    try {
      await api.put("/settings/remote-management", { host, app_public_url: appPublicUrl });
      setSaved(true);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to apply.");
    } finally {
      setSaving(false);
    }
  }

  const applyStage = config?.apply_status?.stage;

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
      <h2 className="mb-1 text-sm font-semibold">Remote Management</h2>
      <p className="mb-4 text-xs text-neutral-500">
        The address agents and your browser use to reach this instance's Remote Management servers. Defaults to this
        host's LAN IP, so machines on the same network work out of the box. To control machines from anywhere, forward
        the ports below to this host and set your public IP or domain here.
      </p>

      <form onSubmit={onSubmit}>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">
          Public host or domain
        </label>
        <input
          className="mb-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={host}
          onChange={(e) => setHost(e.target.value)}
          placeholder="remote.example.com  (or an IP)"
        />
        <p className="mb-3 text-xs text-neutral-400">
          Just the host — no <code>https://</code> or port; DeployCore adds those.
        </p>

        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">
          Instance URL agents use to reach DeployCore itself
        </label>
        <input
          className="mb-1 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={appPublicUrl}
          onChange={(e) => setAppPublicUrl(e.target.value)}
          placeholder="http://192.168.1.50:8000"
        />
        <p className="mb-3 text-xs text-neutral-400">
          A different concern from the relay host above — this is where an installed agent calls back to enroll and
          fetch its config (also what the copy-paste install command downloads the script from). Set by{" "}
          <code>scripts/setup.sh</code> at install time from this host's detected LAN IP; edit it here if that IP has
          since changed, or agents will silently never enroll.
        </p>

        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        {saved && applyStage === "applying" && (
          <div className="mb-3 text-xs text-amber-600 dark:text-amber-400">
            Applying — restarting the relay servers with the new address…
          </div>
        )}
        {applyStage === "done" && !saving && (
          <div className="mb-3 text-xs text-emerald-600 dark:text-emerald-400">Applied.</div>
        )}
        {applyStage === "failed" && (
          <div className="mb-3 text-xs text-red-600">
            Couldn't restart the relay servers automatically ({config?.apply_status?.error || "unknown error"}). The new
            address is saved and used for new agents/sessions; run <code>docker compose up -d rustdesk</code> on the host
            to finish.
          </div>
        )}

        <button
          type="submit"
          className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          disabled={saving || !host.trim()}
        >
          {saving ? "Applying…" : "Apply"}
        </button>
      </form>

      <div className="mt-5 border-t border-neutral-200 pt-4 dark:border-neutral-800">
        <h3 className="mb-2 text-xs font-semibold text-neutral-600 dark:text-neutral-400">
          Ports to forward for internet access
        </h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <tbody className="font-mono">
              {(config?.ports || []).map((p) => (
                <tr key={`${p.port}-${p.proto}`} className="border-t border-neutral-100 dark:border-neutral-800">
                  <td className="py-1 pr-4">{p.port}</td>
                  <td className="py-1 pr-4">{p.proto}</td>
                  <td className="py-1 font-sans text-neutral-500">{p.purpose}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-3 text-xs text-neutral-400">
          <strong>How agents find this address:</strong> at install time each agent asks DeployCore where to connect and
          bakes in whatever this is set to then. Set your public address here <em>before</em> enrolling agents you want
          reachable from outside — agents already enrolled keep their old address until the installer is re-run on them.
        </p>
      </div>
    </div>
  );
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
        autoComplete="new-password"
        className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
        value={clientSecret}
        onChange={(e) => setClientSecret(e.target.value)}
      />
      <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Sender mailbox</label>
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

interface TeamsConfig {
  tenant_id: string;
  client_id: string;
  teams_app_id: string;
  enabled: boolean;
  configured: boolean;
}

function TeamsPanel() {
  const [tenantId, setTenantId] = useState("");
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [teamsAppId, setTeamsAppId] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);

  async function load() {
    const config = await api.get<TeamsConfig>("/settings/global/teams");
    setTenantId(config.tenant_id);
    setClientId(config.client_id);
    setTeamsAppId(config.teams_app_id);
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
      await api.put("/settings/global/teams", {
        tenant_id: tenantId,
        client_id: clientId,
        client_secret: clientSecret || null,
        teams_app_id: teamsAppId,
        enabled,
      });
      setClientSecret("");
      setSaved(true);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save Teams configuration.");
    }
  }

  async function sendTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.post<{ ok: boolean; message: string }>("/settings/global/teams/test");
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof ApiError ? err.message : "Test failed." });
    } finally {
      setTesting(false);
    }
  }

  async function onSubmitChecked(e: FormEvent) {
    if (!tenantId || !clientId || !teamsAppId || (!configured && !clientSecret)) {
      e.preventDefault();
      setError("Tenant ID, client ID, Teams app ID, and a client secret are required.");
      return;
    }
    await onSubmit(e);
  }

  return (
    <form noValidate onSubmit={onSubmitChecked} className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
      <h2 className="mb-1 text-sm font-semibold">Teams notifications (Microsoft 365)</h2>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        Notifies a specific person directly via Microsoft Graph's Activity Feed API (a banner + Activity-tab
        entry, not a raw chat bubble - the app-only-auth-compatible way to message one person without hosting
        a full bot). Two things beyond this form, both on your M365 tenant's own side: the app registration
        needs <code>TeamsActivity.Send</code> and <code>TeamsAppInstallation.ReadWriteForUser.All</code>{" "}
        application permissions (admin-consented), and Teams App ID below must be a Teams app already
        published to your org's app catalog whose manifest declares an activity type named{" "}
        <code>deploymentNotification</code> with template text <code>{"{message}"}</code>. See the
        Documentation tab for the exact manifest snippet. Instance-wide, not per-organization.
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
        autoComplete="new-password"
        className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
        value={clientSecret}
        onChange={(e) => setClientSecret(e.target.value)}
      />
      <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Teams app ID (app catalog)</label>
      <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={teamsAppId} onChange={(e) => setTeamsAppId(e.target.value)} />
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
            {testing ? "Sending..." : "Send test notification"}
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

interface NotificationTemplate {
  event_type: string;
  email_subject: string;
  email_body: string;
  teams_message: string;
}

const NOTIFICATION_EVENT_LABELS: Record<string, string> = {
  start: "Deployment started",
  complete: "Deployment completed",
  failed: "Deployment failed",
};

function NotificationTemplatesPanel() {
  const [templates, setTemplates] = useState<NotificationTemplate[]>([]);
  const [fields, setFields] = useState<Record<string, string[]>>({});
  const [editing, setEditing] = useState<NotificationTemplate | null>(null);

  async function load() {
    const [t, f] = await Promise.all([
      api.get<NotificationTemplate[]>("/settings/global/notification-templates"),
      api.get<Record<string, string[]>>("/settings/global/notification-templates/fields"),
    ]);
    setTemplates(t);
    setFields(f);
  }

  useEffect(() => {
    load();
  }, []);

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
      <h2 className="mb-1 text-sm font-semibold">Notification content</h2>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        The exact subject/body sent for each event, for both email and Teams. Edit any of them to customize
        wording, add your own context, or drop fields you don't care about.
      </p>
      <div className="space-y-2">
        {templates.map((t) => (
          <div key={t.event_type} className="flex items-center justify-between rounded-md border border-neutral-200 px-3 py-2 text-sm dark:border-neutral-700">
            <span>{NOTIFICATION_EVENT_LABELS[t.event_type] ?? t.event_type}</span>
            <button
              type="button"
              className="rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
              onClick={() => setEditing(t)}
            >
              Edit
            </button>
          </div>
        ))}
      </div>

      {editing && (
        <NotificationTemplateModal
          template={editing}
          availableFields={fields[editing.event_type] ?? []}
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

function NotificationTemplateModal({
  template,
  availableFields,
  onClose,
  onSaved,
}: {
  template: NotificationTemplate;
  availableFields: string[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [emailSubject, setEmailSubject] = useState(template.email_subject);
  const [emailBody, setEmailBody] = useState(template.email_body);
  const [teamsMessage, setTeamsMessage] = useState(template.teams_message);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    try {
      await api.put(`/settings/global/notification-templates/${template.event_type}`, {
        email_subject: emailSubject,
        email_body: emailBody,
        teams_message: teamsMessage,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/30 py-8">
      <form noValidate onSubmit={onSubmit} className="w-[32rem] rounded-lg border border-neutral-200 bg-white p-5 shadow-sm dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-1 text-sm font-semibold">{NOTIFICATION_EVENT_LABELS[template.event_type] ?? template.event_type}</h2>
        <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
          Available placeholders: {availableFields.length > 0 ? availableFields.map((f) => `{${f}}`).join(", ") : "none"}.
          An unknown placeholder is left as literal text rather than breaking the notification.
        </p>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Email subject</label>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={emailSubject}
          onChange={(e) => setEmailSubject(e.target.value)}
        />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Email body</label>
        <textarea
          className="mb-3 h-24 w-full resize-y rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={emailBody}
          onChange={(e) => setEmailBody(e.target.value)}
        />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Teams message</label>
        <textarea
          className="mb-3 h-20 w-full resize-y rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={teamsMessage}
          onChange={(e) => setTeamsMessage(e.target.value)}
        />
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" disabled={saving} className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-50">
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </form>
    </div>
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
      <form onSubmit={saveTimeout} className="max-w-md rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-1 text-sm font-semibold">Deployment timeout</h2>
        <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
          A deployment stuck past this many minutes in any non-terminal stage is force-failed automatically.
          Applies to this organization only.
        </p>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Minutes</label>
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
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Value</label>
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
