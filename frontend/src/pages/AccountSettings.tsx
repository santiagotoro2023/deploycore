import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAuth } from "../state/auth";

export default function AccountSettings() {
  return (
    <div className="max-w-md space-y-4">
      <h1 className="text-lg font-semibold">Account</h1>
      <TwoFactorPanel />
      <SessionsPanel />
      <NotificationPreferencesPanel />
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
  const [saved, setSaved] = useState(false);
  const { user } = useAuth();

  async function load() {
    setPrefs(await api.get<NotificationPreferences>("/notification-preferences"));
  }

  useEffect(() => {
    load();
  }, []);

  async function toggle(key: keyof NotificationPreferences) {
    if (!prefs) return;
    const next = { ...prefs, [key]: !prefs[key] };
    setPrefs(next);
    setSaved(false);
    await api.put("/notification-preferences", next);
    setSaved(true);
  }

  if (!prefs) return null;

  const rows: { key: keyof NotificationPreferences; label: string }[] = [
    { key: "email_on_start", label: "Deployment started" },
    { key: "email_on_complete", label: "Deployment completed" },
    { key: "email_on_failed", label: "Deployment failed" },
    { key: "email_on_health_degraded", label: "A completed deployment became unreachable" },
  ];

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5">
      <h2 className="mb-1 text-sm font-semibold">Email notifications</h2>
      <p className="mb-3 text-xs text-neutral-500">
        {user?.email
          ? "Sent to " + user.email + " when Microsoft 365 email is configured and enabled instance-wide."
          : "Add an email address (Users page, or ask an admin) to receive these."}
      </p>
      <div className="space-y-2">
        {rows.map((row) => (
          <label key={row.key} className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={prefs[row.key]} onChange={() => toggle(row.key)} />
            {row.label}
          </label>
        ))}
      </div>
      {saved && <div className="mt-3 text-xs text-emerald-600">Saved.</div>}
    </div>
  );
}

function TwoFactorPanel() {
  const { user } = useAuth();
  const [secret, setSecret] = useState<string | null>(null);
  const [otpauthUrl, setOtpauthUrl] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmed, setConfirmed] = useState(false);

  async function startSetup() {
    setError(null);
    try {
      const res = await api.post<{ secret: string; otpauth_url: string }>("/auth/2fa/setup");
      setSecret(res.secret);
      setOtpauthUrl(res.otpauth_url);
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
      <form onSubmit={disable} className="rounded-lg border border-neutral-200 bg-white p-5">
        <h2 className="mb-1 text-sm font-semibold">Two-factor authentication</h2>
        <p className="mb-3 text-xs text-emerald-600">Enabled on your account.</p>
        <label className="mb-1 block text-xs font-medium text-neutral-600">
          Enter a current code to disable
        </label>
        <input
          className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <button type="submit" className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50">
          Disable 2FA
        </button>
      </form>
    );
  }

  if (confirmed) {
    return (
      <div className="rounded-lg border border-neutral-200 bg-white p-5">
        <h2 className="mb-1 text-sm font-semibold">Two-factor authentication</h2>
        <p className="text-xs text-emerald-600">Enabled. You will be asked for a code on your next sign-in.</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-neutral-200 bg-white p-5">
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
          <p className="mb-2 text-xs text-neutral-500">
            Add this to your authenticator app (manual entry key):
          </p>
          <div className="mb-3 break-all rounded-md bg-neutral-50 p-2 font-mono text-xs">{secret}</div>
          {otpauthUrl && <div className="mb-3 break-all text-xs text-neutral-400">{otpauthUrl}</div>}
          <label className="mb-1 block text-xs font-medium text-neutral-600">Enter the 6-digit code to confirm</label>
          <input
            className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
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
    <div className="rounded-lg border border-neutral-200 bg-white p-5">
      <h2 className="mb-1 text-sm font-semibold">Sessions</h2>
      <p className="mb-3 text-xs text-neutral-500">
        Sign out of every device and browser signed in as you, including this one.
      </p>
      <button
        disabled={busy}
        className="rounded-md border border-red-300 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"
        onClick={onClick}
      >
        Sign out everywhere
      </button>
    </div>
  );
}
