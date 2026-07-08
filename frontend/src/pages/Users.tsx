import { Plus } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import DataTable from "../components/DataTable";
import { Role, User } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Users() {
  const { user: currentUser } = useAuth();
  const { organizations } = useOrg();
  const [users, setUsers] = useState<User[]>([]);
  const [showCreate, setShowCreate] = useState(false);

  const isGlobalAdmin = !!currentUser && roleAtLeast(currentUser.global_role, "admin");

  async function load() {
    if (!isGlobalAdmin) return;
    setUsers(await api.get<User[]>("/users"));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isGlobalAdmin]);

  if (!isGlobalAdmin) {
    return <p className="text-sm text-neutral-500">User management is available to global administrators only.</p>;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Users</h1>
        <button
          className="flex items-center gap-1.5 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800"
          onClick={() => setShowCreate(true)}
        >
          <Plus size={15} strokeWidth={2} />
          New user
        </button>
      </div>

      <DataTable<User>
        rows={users}
        rowKey={(u) => u.id}
        searchValue={(u) => u.email}
        columns={[
          { key: "display_name", header: "Name", render: (u) => u.display_name, sortValue: (u) => u.display_name },
          { key: "email", header: "Email", render: (u) => u.email },
          { key: "global_role", header: "Global role", render: (u) => <Badge value={u.global_role} /> },
          { key: "status", header: "Status", render: (u) => <Badge value={u.is_active ? "active" : "unknown"} /> },
          {
            key: "org_role",
            header: "Assign org role",
            render: (u) => <AssignOrgRole userId={u.id} organizations={organizations} onAssigned={load} />,
          },
        ]}
      />

      {showCreate && (
        <CreateUserForm
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

function AssignOrgRole({
  userId,
  organizations,
  onAssigned,
}: {
  userId: string;
  organizations: { id: string; name: string }[];
  onAssigned: () => void;
}) {
  const [orgId, setOrgId] = useState("");
  const [role, setRole] = useState<Role>("readonly");

  async function assign() {
    if (!orgId) return;
    await api.post(`/users/${userId}/org-roles`, { org_id: orgId, role });
    onAssigned();
  }

  return (
    <div className="flex items-center gap-1">
      <select className="rounded-md border border-neutral-300 px-1.5 py-1 text-xs" value={orgId} onChange={(e) => setOrgId(e.target.value)}>
        <option value="">Org...</option>
        {organizations.map((o) => (
          <option key={o.id} value={o.id}>{o.name}</option>
        ))}
      </select>
      <select className="rounded-md border border-neutral-300 px-1.5 py-1 text-xs" value={role} onChange={(e) => setRole(e.target.value as Role)}>
        <option value="readonly">Readonly</option>
        <option value="operator">Operator</option>
        <option value="admin">Admin</option>
      </select>
      <button className="rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50" onClick={assign}>
        Assign
      </button>
    </div>
  );
}

function CreateUserForm({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [globalRole, setGlobalRole] = useState<Role>("none");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/users", { email, display_name: displayName, password, global_role: globalRole });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create user.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">New user</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Display name</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Email</label>
        <input required type="email" className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={email} onChange={(e) => setEmail(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Password</label>
        <input required type="password" className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={password} onChange={(e) => setPassword(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Global role</label>
        <select className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={globalRole} onChange={(e) => setGlobalRole(e.target.value as Role)}>
          <option value="none">None (org-scoped only)</option>
          <option value="readonly">Readonly</option>
          <option value="operator">Operator</option>
          <option value="admin">Admin</option>
        </select>
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" onClick={onClose}>Cancel</button>
          <button type="submit" className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white">Create</button>
        </div>
      </form>
    </div>
  );
}
