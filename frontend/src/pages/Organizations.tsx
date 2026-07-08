import { Plus } from "lucide-react";
import { FormEvent, useState } from "react";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import DataTable from "../components/DataTable";
import { Organization } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Organizations() {
  const { user } = useAuth();
  const { organizations, refresh } = useOrg();
  const [showCreate, setShowCreate] = useState(false);

  const canCreate = !!user && roleAtLeast(user.global_role, "admin");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Organizations</h1>
        {canCreate && (
          <button
            className="flex items-center gap-1.5 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={15} strokeWidth={2} />
            New organization
          </button>
        )}
      </div>

      <DataTable<Organization>
        rows={organizations}
        rowKey={(o) => o.id}
        searchValue={(o) => o.name}
        columns={[
          { key: "name", header: "Name", render: (o) => o.name, sortValue: (o) => o.name },
          { key: "slug", header: "Slug", render: (o) => o.slug },
          { key: "description", header: "Description", render: (o) => o.description ?? "—" },
          { key: "status", header: "Status", render: (o) => <Badge value={o.is_active ? "active" : "unknown"} /> },
        ]}
      />

      {showCreate && (
        <CreateOrgForm
          onClose={() => setShowCreate(false)}
          onCreated={async () => {
            setShowCreate(false);
            await refresh();
          }}
        />
      )}
    </div>
  );
}

function CreateOrgForm({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post("/organizations", { name, slug, description: description || null });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create organization.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">New organization</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
        <input
          required
          className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Slug</label>
        <input
          required
          className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={slug}
          onChange={(e) => setSlug(e.target.value)}
        />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Description</label>
        <textarea
          className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white">
            Create
          </button>
        </div>
      </form>
    </div>
  );
}
