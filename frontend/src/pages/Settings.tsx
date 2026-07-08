import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useAuth, roleAtLeast } from "../state/auth";
import { useInstanceName } from "../state/instance";
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
      {isGlobalAdmin && <MspOrganizationPanel />}
      <OrgSettingsPanel />
    </div>
  );
}

function MspOrganizationPanel() {
  const currentName = useInstanceName();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => setName(currentName), [currentName]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSaved(false);
    try {
      await api.put("/settings/global/instance_name", { value: name });
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to rename instance.");
    }
  }

  return (
    <form onSubmit={onSubmit} className="max-w-md rounded-lg border border-neutral-200 bg-white p-5">
      <h2 className="mb-1 text-sm font-semibold">MSP Organization</h2>
      <p className="mb-3 text-xs text-neutral-500">
        Your own organization's name — set once during initial setup, shown in the sidebar and sign-in screen.
      </p>
      <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
      <input
        required
        className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
      {saved && <div className="mb-3 text-xs text-emerald-600">Saved.</div>}
      <button type="submit" className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white">
        Save
      </button>
    </form>
  );
}

function OrgSettingsPanel() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [key, setKey] = useState("os_install_timeout_minutes");
  const [value, setValue] = useState("90");
  const [error, setError] = useState<string | null>(null);

  const canManage = roleAtLeast(effectiveRole(selectedOrgId), "admin");

  async function load() {
    if (!selectedOrgId) return;
    setRows(await api.get<SettingRow[]>(`/organizations/${selectedOrgId}/settings`));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  if (!selectedOrgId) return null;

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
        <h2 className="text-sm font-semibold text-neutral-700">Organization overrides</h2>
        <p className="text-sm text-neutral-500">
          Resolved order: deployment override → template override → organization → global. This view shows
          organization and global values for the selected organization.
        </p>
      </div>

      <div className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 bg-white text-sm">
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
        <form onSubmit={onSubmit} className="max-w-md rounded-lg border border-neutral-200 bg-white p-5">
          <h2 className="mb-3 text-sm font-semibold">Set organization override</h2>
          <label className="mb-1 block text-xs font-medium text-neutral-600">Key</label>
          <input className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={key} onChange={(e) => setKey(e.target.value)} />
          <label className="mb-1 block text-xs font-medium text-neutral-600">Value (JSON or plain text)</label>
          <input className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={value} onChange={(e) => setValue(e.target.value)} />
          {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
          <button type="submit" className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white">Save</button>
        </form>
      )}
    </div>
  );
}
