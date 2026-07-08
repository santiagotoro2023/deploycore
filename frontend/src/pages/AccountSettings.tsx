import QRCode from "qrcode";
import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError, getToken } from "../api/client";
import Avatar from "../components/Avatar";
import ConfirmDialog from "../components/ConfirmDialog";
import FileDropzone from "../components/FileDropzone";
import { useAuth } from "../state/auth";

export default function AccountSettings() {
  return (
    <div className="max-w-md space-y-4">
      <h1 className="text-lg font-semibold">Account</h1>
      <ProfilePicturePanel />
      <TwoFactorPanel />
      <SessionsPanel />
      <NotificationPreferencesPanel />
    </div>
  );
}

function ProfilePicturePanel() {
  const { user, refreshUser } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [cacheBust, setCacheBust] = useState(0);
  const [confirmRemove, setConfirmRemove] = useState(false);

  if (!user) return null;

  async function upload(file: File | null) {
    if (!file) return;
    setError(null);
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch("/api/users/me/avatar", {
        method: "PUT",
        headers: { Authorization: `Bearer ${getToken()}` },
        body: formData,
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Upload failed.");
      setCacheBust(Date.now());
      await refreshUser();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  async function remove() {
    await api.delete("/users/me/avatar");
    setConfirmRemove(false);
    await refreshUser();
  }

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5 dark:border-neutral-700 dark:bg-neutral-900">
      <h2 className="mb-1 text-sm font-semibold">Profile picture</h2>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        Shown next to your name in the sidebar and in the users list. PNG or JPEG, under 2 MB.
      </p>
      <div className="mb-3 flex items-center gap-3">
        <Avatar userId={user.id} displayName={user.display_name} hasAvatar={user.has_avatar} size={56} cacheBust={cacheBust} />
        {user.has_avatar && (
          <button className="text-xs text-red-600 hover:underline dark:text-red-400" onClick={() => setConfirmRemove(true)}>
            Remove
          </button>
        )}
      </div>
      <FileDropzone accept=".png,.jpg,.jpeg" hint={uploading ? "Uploading..." : "PNG or JPEG"} onSelect={upload} />
      {error && <div className="mt-3 text-xs text-red-600">{error}</div>}
      <ConfirmDialog
        open={confirmRemove}
        title="Remove profile picture"
        message="Your name's initials will show in its place."
        confirmLabel="Remove"
        onConfirm={remove}
        onCancel={() => setConfirmRemove(false)}
      />
    </div>
  );
}

interface NotificationPreferences {
  email_on_start: boolean;
  email_on_complete: boolean;
  email_on_failed: boolean;
  email_on_health_degraded: boolean;
}

function NotificationPreferencesPanel() {
  const [prefs, setPrefs] = useState<NotificationPreferences | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const { user } = useAuth();

  async function load() {
    setPrefs(await api.get<NotificationPreferences>("/notification-preferences"));
  }

  useEffect(() => {
    load();
  }, []);

  function toggle(key: keyof NotificationPreferences) {
    if (!prefs) return;
    setPrefs({ ...prefs, [key]: !prefs[key] });
    setDirty(true);
    setSaved(false);
  }

  async function applyChanges() {
    if (!prefs) return;
    setSaving(true);
    try {
      await api.put("/notification-preferences", prefs);
      setDirty(false);
      setSaved(true);
    } finally {
      setSaving(false);
    }
  }

  if (!prefs) return null;

  const rows: { key: keyof NotificationPreferences; label: string }[] = [
    { key: "email_on_start", label: "Deployment started" },
    { key: "email_on_complete", label: "Deployment completed" },
    { key: "email_on_failed", label: "Deployment failed" },
    { key: "email_on_health_degraded", label: "A completed deployment became unreachable" },
  ];

  return (
    <div className="rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5">
      <h2 className="mb-1 text-sm font-semibold">Email notifications</h2>
      <p className="mb-3 text-xs text-neutral-500 dark:text-neutral-400">
        {user?.email
          ? "Sent to " + user.email + " when Microsoft 365 email is configured and enabled instance-wide."
          : "Add an email address (Users page, or ask an admin) to receive these."}
      </p>
      <div className="mb-3 space-y-2">
        {rows.map((row) => (
          <label key={row.key} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={prefs[row.key]} onChange={() => toggle(row.key)} />
            {row.label}
          </label>
        ))}
      </div>
      <button
        disabled={!dirty || saving}
        className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        onClick={applyChanges}
      >
        {saving ? "Saving..." : "Apply changes"}
      </button>
      {saved && <div className="mt-3 text-xs text-emerald-600">Saved.</div>}
    </div>
  );
}

function TwoFactorPanel() {
  const { user } = useAuth();
  const [secret, setSecret] = useState<string | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  async function startSetup() {
    setError(null);
    try {
      const res = await api.post<{ secret: string; otpauth_url: string }>("/auth/2fa/setup");
      setSecret(res.secret);
      // Generated entirely client-side (no network call to a third-party
      // QR service), same self-hosted posture as the rest of the app.
      setQrDataUrl(await QRCode.toDataURL(res.otpauth_url, { margin: 1, width: 200 }));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to start 2FA setup.");
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/auth/2fa/confirm", { code });
      setConfirmed(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Invalid code.");
    }
  }

  async function disable(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/auth/2fa/disable", { code });
      window.location.reload();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Invalid code.");
    }
  }

  if (user?.totp_enabled && !secret) {
    return (
      <form onSubmit={disable} className="rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5">
        <h2 className="mb-1 text-sm font-semibold">Two-factor authentication</h2>
        <p className="mb-3 text-xs text-emerald-600 dark:text-emerald-400">Enabled on your account.</p>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">
          Enter a current code to disable
        </label>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <button type="submit" className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950">
          Disable 2FA
        </button>
      </form>
    );
  }

  if (confirmed) {
    return (
      <div className="rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5">
        <h2 className="mb-1 text-sm font-semibold">Two-factor authentication</h2>
        <p className="text-xs text-emerald-600">Enabled. You will be asked for a code on your next sign-in.</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5">
      <h2 className="mb-1 text-sm font-semibold">Two-factor authentication</h2>
      <p className="mb-3 text-xs text-neutral-500">
        Adds a time-based code from an authenticator app to sign-in.
      </p>
      {!secret && (
        <button className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700" onClick={startSetup}>
          Set up 2FA
        </button>
      )}
      {secret && (
        <form onSubmit={confirm}>
          <p className="mb-2 text-xs text-neutral-500 dark:text-neutral-400">
            Scan this with your authenticator app (Google Authenticator, Microsoft Authenticator, 1Password,
            etc.):
          </p>
          {qrDataUrl && (
            <div className="mb-3 inline-block rounded-md border border-neutral-200 bg-white p-2 dark:border-neutral-700">
              <img src={qrDataUrl} alt="2FA QR code" width={200} height={200} />
            </div>
          )}
          <details className="mb-3">
            <summary className="cursor-pointer text-xs text-neutral-500 hover:text-neutral-700 dark:text-neutral-400 dark:hover:text-neutral-200">
              Can't scan it? Enter this key manually
            </summary>
            <div className="mt-2 break-all rounded-md bg-neutral-50 p-2 font-mono text-xs dark:bg-neutral-800">{secret}</div>
          </details>
          <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Enter the 6-digit code to confirm</label>
          <input
            className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">
            Confirm
          </button>
        </form>
      )}
    </div>
  );
}

function SessionsPanel() {
  const { logoutEverywhere } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);

  async function onClick() {
    setBusy(true);
    try {
      await logoutEverywhere();
      navigate("/login");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5">
      <h2 className="mb-1 text-sm font-semibold">Sessions</h2>
      <p className="mb-3 text-xs text-neutral-500">
        Sign out of every device and browser signed in as you, including this one.
      </p>
      <button
        disabled={busy}
        className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
        onClick={onClick}
      >
        Sign out everywhere
      </button>
    </div>
  );
}
