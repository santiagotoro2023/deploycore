import { Plug, Plus } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import DataTable from "../components/DataTable";
import { HypervisorHost, HypervisorType } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Hypervisors() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [hosts, setHosts] = useState<HypervisorHost[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);

  async function load() {
    if (!selectedOrgId) return;
    setHosts(await api.get<HypervisorHost[]>(`/organizations/${selectedOrgId}/hypervisors`));
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
            className="flex items-center gap-1.5 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800"
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
          { key: "status", header: "Status", render: (h) => <Badge value={h.last_test_status} /> },
          {
            key: "actions",
            header: "",
            render: (h) =>
              canManage && (
                <button
                  className="flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50 disabled:opacity-50"
                  disabled={testingId === h.id}
                  onClick={() => testConnection(h.id)}
                >
                  <Plug size={13} strokeWidth={1.75} />
                  {testingId === h.id ? "Testing..." : "Test connection"}
                </button>
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
  const [defaultNetwork, setDefaultNetwork] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post(`/organizations/${orgId}/hypervisors`, {
        name,
        type,
        api_endpoint: apiEndpoint,
        username,
        credential,
        tls_verify: tlsVerify,
        default_datastore: defaultDatastore || null,
        default_network: defaultNetwork || null,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create hypervisor.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">New hypervisor</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={name} onChange={(e) => setName(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Type</label>
        <select className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={type} onChange={(e) => setType(e.target.value as HypervisorType)}>
          <option value="esxi">ESXi</option>
          <option value="proxmox">Proxmox (not yet implemented)</option>
        </select>
        <label className="mb-1 block text-xs font-medium text-neutral-600">API endpoint</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={apiEndpoint} onChange={(e) => setApiEndpoint(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Username</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={username} onChange={(e) => setUsername(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Password</label>
        <input required type="password" className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={credential} onChange={(e) => setCredential(e.target.value)} />
        <div className="mb-3 grid grid-cols-2 gap-3">
          <input placeholder="Default datastore" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={defaultDatastore} onChange={(e) => setDefaultDatastore(e.target.value)} />
          <input placeholder="Default network" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={defaultNetwork} onChange={(e) => setDefaultNetwork(e.target.value)} />
        </div>
        <label className="mb-3 flex items-center gap-2 text-xs font-medium text-neutral-600">
          <input type="checkbox" checked={tlsVerify} onChange={(e) => setTlsVerify(e.target.checked)} />
          Verify TLS certificate
        </label>
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" onClick={onClose}>Cancel</button>
          <button type="submit" className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white">Create</button>
        </div>
      </form>
    </div>
  );
}
