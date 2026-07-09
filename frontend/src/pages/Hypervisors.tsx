import { Plug, Plus, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import ConfirmDialog from "../components/ConfirmDialog";
import DataTable from "../components/DataTable";
import Select from "../components/Select";
import { HypervisorHost, HypervisorType } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Hypervisors() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [hosts, setHosts] = useState<HypervisorHost[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<HypervisorHost | null>(null);

  async function load() {
    if (!selectedOrgId) return;
    setHosts(await api.get<HypervisorHost[]>(`/organizations/${selectedOrgId}/hypervisors`));
  }

  async function deleteHost() {
    if (!confirmDelete || !selectedOrgId) return;
    await api.delete(`/organizations/${selectedOrgId}/hypervisors/${confirmDelete.id}`);
    setConfirmDelete(null);
    await load();
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;
  const canManage = roleAtLeast(effectiveRole(selectedOrgId), "admin");

  async function testConnection(hostId: string) {
    setTestingId(hostId);
    try {
      await api.post(`/organizations/${selectedOrgId}/hypervisors/${hostId}/test-connection`);
    } finally {
      setTestingId(null);
      await load();
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Hypervisors</h1>
        {canManage && (
          <button
            className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={15} strokeWidth={2} />
            New hypervisor
          </button>
        )}
      </div>

      <DataTable<HypervisorHost>
        rows={hosts}
        rowKey={(h) => h.id}
        searchValue={(h) => h.name}
        columns={[
          { key: "name", header: "Name", render: (h) => h.name, sortValue: (h) => h.name },
          { key: "type", header: "Type", render: (h) => h.type },
          { key: "endpoint", header: "Endpoint", render: (h) => h.api_endpoint },
          { key: "status", header: "Status", render: (h) => <Badge value={h.last_test_status} />, shrink: true },
          {
            key: "actions",
            header: "",
            render: (h) =>
              canManage && (
                <div className="flex items-center gap-2">
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50"
                    disabled={testingId === h.id}
                    onClick={() => testConnection(h.id)}
                  >
                    <Plug size={13} strokeWidth={1.75} />
                    {testingId === h.id ? "Testing..." : "Test connection"}
                  </button>
                  <button
                    className="flex items-center gap-1 rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                    onClick={() => setConfirmDelete(h)}
                  >
                    <Trash2 size={13} strokeWidth={1.75} />
                  </button>
                </div>
              ),
          },
        ]}
      />

      {showCreate && (
        <CreateHypervisorForm
          orgId={selectedOrgId}
          onClose={() => setShowCreate(false)}
          onCreated={async () => {
            setShowCreate(false);
            await load();
          }}
        />
      )}

      <ConfirmDialog
        open={!!confirmDelete}
        title="Delete hypervisor"
        message={`Delete "${confirmDelete?.name}"? This only removes DeployCore's stored connection and credentials, it does not touch the hypervisor or any VMs already created on it. This cannot be undone.`}
        confirmLabel="Delete"
        onConfirm={deleteHost}
        onCancel={() => setConfirmDelete(null)}
      />
    </div>
  );
}

function CreateHypervisorForm({
  orgId,
  onClose,
  onCreated,
}: {
  orgId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [type, setType] = useState<HypervisorType>("esxi");
  const [apiEndpoint, setApiEndpoint] = useState("");
  const [username, setUsername] = useState("");
  const [credential, setCredential] = useState("");
  const [tlsVerify, setTlsVerify] = useState(true);
  const [defaultDatastore, setDefaultDatastore] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [testing, setTesting] = useState(false);

  function currentValues() {
    return {
      name: name || "(unnamed)",
      type,
      api_endpoint: apiEndpoint,
      username,
      credential,
      tls_verify: tlsVerify,
    };
  }

  async function testConnection() {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.post<{ ok: boolean; message: string }>(
        `/organizations/${orgId}/hypervisors/test-connection`,
        currentValues(),
      );
      setTestResult(result);
    } catch (err) {
      setTestResult({ ok: false, message: err instanceof ApiError ? err.message : "Test failed." });
    } finally {
      setTesting(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name || !apiEndpoint || !username || !credential) {
      setError("Name, API endpoint, username, and password are required.");
      return;
    }
    try {
      await api.post(`/organizations/${orgId}/hypervisors`, {
        name,
        type,
        api_endpoint: apiEndpoint,
        username,
        credential,
        tls_verify: tlsVerify,
        default_datastore: defaultDatastore || null,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create hypervisor.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form noValidate onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white p-5 shadow-sm dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-4 text-sm font-semibold">New hypervisor</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Name</label>
        <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={name} onChange={(e) => setName(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Type</label>
        <Select className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" value={type} onChange={(e) => setType(e.target.value as HypervisorType)}>
          <option value="esxi">ESXi</option>
        </Select>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">API endpoint (IP address or hostname)</label>
        <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={apiEndpoint} onChange={(e) => setApiEndpoint(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Username</label>
        <input autoComplete="off" className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={username} onChange={(e) => setUsername(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Password</label>
        <input type="password" autoComplete="new-password" className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={credential} onChange={(e) => setCredential(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Default datastore</label>
        <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={defaultDatastore} onChange={(e) => setDefaultDatastore(e.target.value)} />
        <label className="mb-3 flex items-center gap-2 text-xs font-medium text-neutral-600 dark:text-neutral-400">
          <input type="checkbox" checked={tlsVerify} onChange={(e) => setTlsVerify(e.target.checked)} />
          Verify TLS certificate
        </label>

        <button
          type="button"
          className="mb-3 flex w-full items-center justify-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50"
          disabled={testing || !apiEndpoint || !username || !credential}
          onClick={testConnection}
        >
          <Plug size={14} strokeWidth={1.75} />
          {testing ? "Testing..." : "Test connection"}
        </button>
        {testResult && (
          <div className={`mb-3 rounded-md border p-2 text-xs ${testResult.ok ? "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-400" : "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400"}`}>
            {testResult.message}
          </div>
        )}

        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" onClick={onClose}>Cancel</button>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">Create</button>
        </div>
      </form>
    </div>
  );
}
