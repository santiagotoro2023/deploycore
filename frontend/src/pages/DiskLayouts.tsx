import { Pencil, Plus, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import DataTable from "../components/DataTable";
import { DiskLayout } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

interface ExtraVolumeForm {
  label: string;
  drive_letter: string;
  size_mb: number;
}

export default function DiskLayouts() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [layouts, setLayouts] = useState<DiskLayout[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<DiskLayout | null>(null);

  async function load() {
    if (!selectedOrgId) return;
    setLayouts(await api.get<DiskLayout[]>(`/organizations/${selectedOrgId}/disk-layouts`));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;
  const canManage = roleAtLeast(effectiveRole(selectedOrgId), "operator");

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Disk Layouts</h1>
        {canManage && (
          <button
            className="flex items-center gap-1.5 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={15} strokeWidth={2} />
            New disk layout
          </button>
        )}
      </div>

      <DataTable<DiskLayout>
        rows={layouts}
        rowKey={(l) => l.id}
        searchValue={(l) => l.name}
        columns={[
          { key: "name", header: "Name", render: (l) => l.name, sortValue: (l) => l.name },
          { key: "scope", header: "Scope", render: (l) => (l.org_id ? "Organization" : "Global") },
          {
            key: "os_volume",
            header: "OS volume",
            render: (l) => (l.layout_json.os_volume === "remaining" ? "Remaining space" : `${l.layout_json.os_volume.size_mb} MB`),
          },
          { key: "extra_volumes", header: "Extra volumes", render: (l) => l.layout_json.extra_volumes.length },
          {
            key: "actions",
            header: "",
            render: (l) =>
              canManage &&
              l.org_id === selectedOrgId && (
                <button
                  className="flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50"
                  onClick={() => setEditing(l)}
                >
                  <Pencil size={12} strokeWidth={1.75} />
                  Edit
                </button>
              ),
          },
        ]}
      />

      {showCreate && (
        <DiskLayoutForm
          orgId={selectedOrgId}
          onClose={() => setShowCreate(false)}
          onSaved={async () => {
            setShowCreate(false);
            await load();
          }}
        />
      )}
      {editing && (
        <DiskLayoutForm
          orgId={selectedOrgId}
          existing={editing}
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

function DiskLayoutForm({
  orgId,
  existing,
  onClose,
  onSaved,
}: {
  orgId: string;
  existing?: DiskLayout;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!existing;
  const [name, setName] = useState(existing?.name ?? "");
  const [efiSizeMb, setEfiSizeMb] = useState(existing?.layout_json.efi_size_mb ?? 500);
  const [msrSizeMb, setMsrSizeMb] = useState(existing?.layout_json.msr_size_mb ?? 128);
  const [osVolumeMode, setOsVolumeMode] = useState<"remaining" | "fixed">(
    existing && existing.layout_json.os_volume !== "remaining" ? "fixed" : "remaining",
  );
  const [osVolumeSizeMb, setOsVolumeSizeMb] = useState(
    existing && existing.layout_json.os_volume !== "remaining" ? existing.layout_json.os_volume.size_mb : 102400,
  );
  const [extraVolumes, setExtraVolumes] = useState<ExtraVolumeForm[]>(existing?.layout_json.extra_volumes ?? []);
  const [error, setError] = useState<string | null>(null);

  function addVolume() {
    setExtraVolumes([...extraVolumes, { label: "Data", drive_letter: "D", size_mb: 51200 }]);
  }

  function updateVolume(index: number, patch: Partial<ExtraVolumeForm>) {
    setExtraVolumes(extraVolumes.map((v, i) => (i === index ? { ...v, ...patch } : v)));
  }

  function removeVolume(index: number) {
    setExtraVolumes(extraVolumes.filter((_, i) => i !== index));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const body = {
      name,
      layout: {
        efi_size_mb: efiSizeMb,
        msr_size_mb: msrSizeMb,
        os_volume: osVolumeMode === "remaining" ? "remaining" : { size_mb: osVolumeSizeMb },
        extra_volumes: extraVolumes,
      },
    };
    try {
      if (isEdit) {
        await api.patch(`/organizations/${orgId}/disk-layouts/${existing!.id}`, body);
      } else {
        await api.post(`/organizations/${orgId}/disk-layouts`, body);
      }
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to save disk layout.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto bg-black/30 py-8">
      <form onSubmit={onSubmit} className="w-[28rem] rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">{isEdit ? `Edit ${existing!.name}` : "New disk layout"}</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
        <input
          required
          className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <div className="mb-3 grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">EFI size (MB)</label>
            <input
              type="number"
              className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
              value={efiSizeMb}
              onChange={(e) => setEfiSizeMb(Number(e.target.value))}
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600">MSR size (MB)</label>
            <input
              type="number"
              className="w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
              value={msrSizeMb}
              onChange={(e) => setMsrSizeMb(Number(e.target.value))}
            />
          </div>
        </div>
        <label className="mb-1 block text-xs font-medium text-neutral-600">OS volume</label>
        <select
          className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
          value={osVolumeMode}
          onChange={(e) => setOsVolumeMode(e.target.value as "remaining" | "fixed")}
        >
          <option value="remaining">Remaining disk space</option>
          <option value="fixed">Fixed size</option>
        </select>
        {osVolumeMode === "fixed" && (
          <input
            type="number"
            placeholder="Size (MB)"
            className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm"
            value={osVolumeSizeMb}
            onChange={(e) => setOsVolumeSizeMb(Number(e.target.value))}
          />
        )}

        <div className="mb-3">
          <div className="mb-1 flex items-center justify-between">
            <label className="block text-xs font-medium text-neutral-600">Additional volumes</label>
            <button
              type="button"
              className="flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-900"
              onClick={addVolume}
            >
              <Plus size={13} strokeWidth={2} />
              Add
            </button>
          </div>
          {extraVolumes.length === 0 && <p className="text-xs text-neutral-400">No additional volumes.</p>}
          {extraVolumes.map((v, i) => (
            <div key={i} className="mb-2 grid grid-cols-[1fr_4rem_5rem_auto] items-center gap-2">
              <input
                placeholder="Label"
                className="rounded-md border border-neutral-300 px-2 py-1 text-sm"
                value={v.label}
                onChange={(e) => updateVolume(i, { label: e.target.value })}
              />
              <input
                placeholder="Letter"
                maxLength={1}
                className="rounded-md border border-neutral-300 px-2 py-1 text-sm uppercase"
                value={v.drive_letter}
                onChange={(e) => updateVolume(i, { drive_letter: e.target.value.toUpperCase() })}
              />
              <input
                type="number"
                placeholder="MB"
                className="rounded-md border border-neutral-300 px-2 py-1 text-sm"
                value={v.size_mb}
                onChange={(e) => updateVolume(i, { size_mb: Number(e.target.value) })}
              />
              <button
                type="button"
                className="flex h-7 w-7 items-center justify-center rounded-md text-neutral-400 hover:bg-neutral-100 hover:text-red-600"
                onClick={() => removeVolume(i)}
              >
                <Trash2 size={14} strokeWidth={1.75} />
              </button>
            </div>
          ))}
        </div>

        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white">
            {isEdit ? "Save" : "Create"}
          </button>
        </div>
      </form>
    </div>
  );
}
