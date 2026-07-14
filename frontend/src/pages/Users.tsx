import { KeyRound, LogOut, Pencil, Plus, Power, Trash2, X } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import Avatar from "../components/Avatar";
import Badge from "../components/Badge";
import ConfirmDialog from "../components/ConfirmDialog";
import DataTable from "../components/DataTable";
import Select from "../components/Select";
import { Role, User } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Users() {
  const { user: currentUser } = useAuth();
  const { organizations } = useOrg();
  const [users, setUsers] = useState<User[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [confirmLogout, setConfirmLogout] = useState<User | null>(null);
  const [confirmReset2fa, setConfirmReset2fa] = useState<User | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<User | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const isGlobalAdmin = !!currentUser && roleAtLeast(currentUser.global_role, "admin");

  async function load() {
    if (!isGlobalAdmin) return;
    try {
      setUsers(await api.get<User[]>("/users"));
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isGlobalAdmin]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2500);
    return () => clearTimeout(t);
  }, [toast]);

  if (!isGlobalAdmin) {
    return <p className="text-sm text-neutral-500">User management is available to global administrators only.</p>;
  }

  function orgName(orgId: string): string {
    return organizations.find((o) => o.id === orgId)?.name ?? orgId;
  }

  async function removeOrgRole(userId: string, orgId: string) {
    await api.delete(`/users/${userId}/org-roles/${orgId}`);
    setToast("Organization role removed.");
    await load();
  }

  async function removeGlobalRole(userId: string) {
    await api.patch(`/users/${userId}`, { global_role: "none" });
    setToast("Global role removed.");
    await load();
  }

  async function forceLogout() {
    if (!confirmLogout) return;
    await api.post(`/users/${confirmLogout.id}/force-logout`);
    setConfirmLogout(null);
    setToast(`Signed out ${confirmLogout.username} everywhere.`);
  }

  async function reset2fa() {
    if (!confirmReset2fa) return;
    await api.post(`/users/${confirmReset2fa.id}/2fa/disable`);
    setConfirmReset2fa(null);
    setToast(`2FA reset for ${confirmReset2fa.username}.`);
    await load();
  }

  async function toggleActive(u: User) {
    await api.patch(`/users/${u.id}`, { is_active: !u.is_active });
    setToast(u.is_active ? `${u.username} deactivated.` : `${u.username} activated.`);
    await load();
  }

  async function deleteUser() {
    if (!confirmDelete) return;
    setDeleteError(null);
    try {
      await api.delete(`/users/${confirmDelete.id}`);
      setToast(`${confirmDelete.username} deleted.`);
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : "Failed to delete user.");
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Users</h1>
        <button
          className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
          onClick={() => setShowCreate(true)}
        >
          <Plus size={15} strokeWidth={2} />
          New user
        </button>
      </div>

      {toast && (
        <div className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-400">
          {toast}
        </div>
      )}

      <DataTable<User>
        rows={users}
        loading={!loaded}
        rowKey={(u) => u.id}
        searchValue={(u) => u.username}
        columns={[
          { key: "username", header: "Username", render: (u) => u.username, sortValue: (u) => u.username, shrink: true },
          {
            key: "display_name",
            header: "Name",
            render: (u) => (
              <div className="flex items-center gap-2">
                <Avatar userId={u.id} displayName={u.display_name} hasAvatar={u.has_avatar} size={24} />
                {u.display_name}
              </div>
            ),
            sortValue: (u) => u.display_name,
          },
          { key: "email", header: "Email", render: (u) => u.email ?? "(none)" },
          { key: "global_role", header: "Global role", render: (u) => <Badge value={u.global_role} />, shrink: true },
          { key: "status", header: "Status", render: (u) => <Badge value={u.is_active ? "active" : "unknown"} />, shrink: true },
          {
            key: "org_roles",
            header: "Access",
            render: (u) => (
              <div className="flex flex-wrap items-center gap-1">
                {u.global_role !== "none" && (
                  <span className="flex items-center gap-1 rounded-full border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-400">
                    Global: {u.global_role}
                    <button className="text-blue-400 hover:text-red-600" onClick={() => removeGlobalRole(u.id)}>
                      <X size={11} strokeWidth={2} />
                    </button>
                  </span>
                )}
                {u.global_role === "none" && Object.entries(u.org_roles).length === 0 && (
                  <span className="text-xs text-neutral-400">none</span>
                )}
                {Object.entries(u.org_roles).map(([orgId, role]) => (
                  <span
                    key={orgId}
                    className="flex items-center gap-1 rounded-full border border-neutral-200 bg-neutral-50 px-2 py-0.5 text-xs dark:border-neutral-700 dark:bg-neutral-800"
                  >
                    {orgName(orgId)}: {role}
                    <button className="text-neutral-400 hover:text-red-600" onClick={() => removeOrgRole(u.id, orgId)}>
                      <X size={11} strokeWidth={2} />
                    </button>
                  </span>
                ))}
                <AssignOrgRole
                  userId={u.id}
                  organizations={organizations}
                  onAssigned={() => { setToast("Role assigned."); load(); }}
                />
              </div>
            ),
          },
          {
            key: "actions",
            header: "",
            shrink: true,
            render: (u) => (
              <div className="flex items-center gap-2">
                <button
                  className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                  onClick={() => setEditing(u)}
                >
                  <Pencil size={12} strokeWidth={1.75} />
                  Edit
                </button>
                <button
                  className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                  onClick={() => toggleActive(u)}
                >
                  <Power size={12} strokeWidth={1.75} />
                  {u.is_active ? "Deactivate" : "Activate"}
                </button>
                <button
                  className="flex items-center gap-1 rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                  onClick={() => setConfirmLogout(u)}
                >
                  <LogOut size={12} strokeWidth={1.75} />
                  Force logout
                </button>
                {u.totp_enabled && (
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                    onClick={() => setConfirmReset2fa(u)}
                  >
                    <KeyRound size={12} strokeWidth={1.75} />
                    Reset 2FA
                  </button>
                )}
                {u.id !== currentUser?.id && (
                  <button
                    className="flex items-center gap-1 rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                    onClick={() => setConfirmDelete(u)}
                  >
                    <Trash2 size={12} strokeWidth={1.75} />
                    Delete
                  </button>
                )}
              </div>
            ),
          },
        ]}
      />

      {showCreate && (
        <CreateUserForm
          onClose={() => setShowCreate(false)}
          onCreated={async () => {
            setShowCreate(false);
            setToast("User created.");
            await load();
          }}
        />
      )}

      {editing && (
        <EditUserForm
          user={editing}
          onClose={() => setEditing(null)}
          onSaved={async () => {
            setEditing(null);
            setToast("User updated.");
            await load();
          }}
        />
      )}

      <ConfirmDialog
        open={!!confirmLogout}
        title="Force logout"
        message={`Immediately sign out every active session for ${confirmLogout?.username}. They'll need to log in again.`}
        confirmLabel="Sign out everywhere"
        onConfirm={forceLogout}
        onCancel={() => setConfirmLogout(null)}
      />

      <ConfirmDialog
        open={!!confirmReset2fa}
        title="Reset 2FA"
        message={`Disable two-factor authentication for ${confirmReset2fa?.username}. They'll be able to sign in with just their password, and can set 2FA up again themselves afterward. Use this if they've lost their authenticator device.`}
        confirmLabel="Reset 2FA"
        onConfirm={reset2fa}
        onCancel={() => setConfirmReset2fa(null)}
      />

      <ConfirmDialog
        open={!!confirmDelete}
        title="Delete user"
        message={
          <>
            {`Permanently deletes ${confirmDelete?.username}'s account. This cannot be undone — use Deactivate instead to only block sign-in.`}
            {deleteError && <p className="mt-2 text-red-600">{deleteError}</p>}
          </>
        }
        confirmLabel="Delete"
        onConfirm={deleteUser}
        onCancel={() => {
          setConfirmDelete(null);
          setDeleteError(null);
        }}
      />
    </div>
  );
}

const GLOBAL_SENTINEL = "__global__";

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
    if (orgId === GLOBAL_SENTINEL) {
      await api.patch(`/users/${userId}`, { global_role: role });
    } else {
      await api.post(`/users/${userId}/org-roles`, { org_id: orgId, role });
    }
    setOrgId("");
    onAssigned();
  }

  return (
    <div className="flex items-center gap-1">
      <Select className="rounded-md border border-neutral-300 dark:border-neutral-700 px-1.5 py-1 text-xs" value={orgId} onChange={(e) => setOrgId(e.target.value)}>
        <option value="">Assign...</option>
        <option value={GLOBAL_SENTINEL}>Global (all organizations)</option>
        {organizations.map((o) => (
          <option key={o.id} value={o.id}>{o.name}</option>
        ))}
      </Select>
      <Select className="rounded-md border border-neutral-300 dark:border-neutral-700 px-1.5 py-1 text-xs" value={role} onChange={(e) => setRole(e.target.value as Role)}>
        <option value="readonly">Readonly</option>
        <option value="operator">Operator</option>
        <option value="admin">Admin</option>
      </Select>
      <button
        className="rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
        disabled={!orgId}
        onClick={assign}
      >
        Assign
      </button>
    </div>
  );
}

function CreateUserForm({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [globalRole, setGlobalRole] = useState<Role>("none");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username || !displayName || !password) {
      setError("Username, display name, and password are required.");
      return;
    }
    try {
      await api.post("/users", {
        username,
        email: email || null,
        display_name: displayName,
        password,
        global_role: globalRole,
      });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create user.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form noValidate onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white p-5 shadow-sm dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-4 text-sm font-semibold">New user</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Username</label>
        <input autoComplete="off" className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={username} onChange={(e) => setUsername(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Display name</label>
        <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Email (optional)</label>
        <input type="email" autoComplete="off" className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={email} onChange={(e) => setEmail(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Password</label>
        <input type="password" autoComplete="new-password" className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={password} onChange={(e) => setPassword(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Global role</label>
        <Select className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" value={globalRole} onChange={(e) => setGlobalRole(e.target.value as Role)}>
          <option value="none">None (org-scoped only)</option>
          <option value="readonly">Readonly</option>
          <option value="operator">Operator</option>
          <option value="admin">Admin</option>
        </Select>
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" onClick={onClose}>Cancel</button>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">Create</button>
        </div>
      </form>
    </div>
  );
}

function EditUserForm({ user, onClose, onSaved }: { user: User; onClose: () => void; onSaved: () => void }) {
  const [displayName, setDisplayName] = useState(user.display_name);
  const [email, setEmail] = useState(user.email ?? "");
  const [globalRole, setGlobalRole] = useState<Role>(user.global_role);
  const [isActive, setIsActive] = useState(user.is_active);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!displayName) {
      setError("Display name is required.");
      return;
    }
    try {
      const body: Record<string, unknown> = {
        display_name: displayName,
        email: email || null,
        global_role: globalRole,
        is_active: isActive,
      };
      if (password) body.password = password;
      await api.patch(`/users/${user.id}`, body);
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update user.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form noValidate onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white p-5 shadow-sm dark:border-neutral-700 dark:bg-neutral-900">
        <h2 className="mb-4 text-sm font-semibold">Edit {user.username}</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Display name</label>
        <input className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Email</label>
        <input type="email" className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={email} onChange={(e) => setEmail(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Global role</label>
        <Select className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" value={globalRole} onChange={(e) => setGlobalRole(e.target.value as Role)}>
          <option value="none">None (org-scoped only)</option>
          <option value="readonly">Readonly</option>
          <option value="operator">Operator</option>
          <option value="admin">Admin</option>
        </Select>
        <label className="mb-3 flex items-center gap-2 text-xs font-medium text-neutral-600 dark:text-neutral-400">
          <input type="checkbox" checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />
          Active (unchecking blocks sign-in)
        </label>
        <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">New password (leave blank to keep current)</label>
        <input type="password" autoComplete="new-password" className="mb-3 w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900" value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm" onClick={onClose}>Cancel</button>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700">Save</button>
        </div>
      </form>
    </div>
  );
}
