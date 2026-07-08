import { UploadCloud } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError, getToken } from "../api/client";
import Badge from "../components/Badge";
import DataTable from "../components/DataTable";
import { IsoAsset, IsoKind } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

const CHUNK_SIZE = 8 * 1024 * 1024;

export default function IsoAssets() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [isos, setIsos] = useState<IsoAsset[]>([]);
  const [showUpload, setShowUpload] = useState(false);

  async function load() {
    if (!selectedOrgId) return;
    setIsos(await api.get<IsoAsset[]>(`/organizations/${selectedOrgId}/iso-assets`));
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
        <h1 className="text-lg font-semibold">ISO Assets</h1>
        {canManage && (
          <button
            className="flex items-center gap-1.5 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800"
            onClick={() => setShowUpload(true)}
          >
            <UploadCloud size={15} strokeWidth={1.75} />
            Upload ISO
          </button>
        )}
      </div>

      <DataTable<IsoAsset>
        rows={isos}
        rowKey={(i) => i.id}
        searchValue={(i) => i.filename}
        columns={[
          { key: "filename", header: "Filename", render: (i) => i.filename, sortValue: (i) => i.filename },
          { key: "kind", header: "Kind", render: (i) => (i.kind === "windows_iso" ? "Windows Server ISO" : "VirtIO driver ISO") },
          { key: "scope", header: "Scope", render: (i) => (i.org_id ? "Organization" : "Global") },
          { key: "size", header: "Size", render: (i) => (i.size_bytes ? `${(i.size_bytes / 1e9).toFixed(2)} GB` : "—") },
          { key: "status", header: "Status", render: (i) => <Badge value={i.upload_status} /> },
        ]}
      />

      {showUpload && (
        <UploadIsoForm
          orgId={selectedOrgId}
          onClose={() => setShowUpload(false)}
          onDone={async () => {
            setShowUpload(false);
            await load();
          }}
        />
      )}
    </div>
  );
}

function UploadIsoForm({ orgId, onClose, onDone }: { orgId: string; onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState<IsoKind>("windows_iso");
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setError(null);
    setUploading(true);
    try {
      const iso = await api.post<IsoAsset>(`/organizations/${orgId}/iso-assets`, { filename: file.name, kind });
      let offset = 0;
      while (offset < file.size) {
        const chunk = file.slice(offset, offset + CHUNK_SIZE);
        const res = await fetch(`/api/organizations/${orgId}/iso-assets/${iso.id}/chunk`, {
          method: "POST",
          headers: { Authorization: `Bearer ${getToken()}`, "Content-Type": "application/octet-stream" },
          body: chunk,
        });
        if (!res.ok) throw new Error(`Chunk upload failed at offset ${offset}`);
        offset += CHUNK_SIZE;
        setProgress(Math.min(100, Math.round((offset / file.size) * 100)));
      }
      await api.post(`/organizations/${orgId}/iso-assets/${iso.id}/finalize`);
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">Upload ISO</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Kind</label>
        <select className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={kind} onChange={(e) => setKind(e.target.value as IsoKind)}>
          <option value="windows_iso">Windows Server ISO</option>
          <option value="virtio_iso">VirtIO driver ISO</option>
        </select>
        <label className="mb-1 block text-xs font-medium text-neutral-600">File</label>
        <input
          required
          type="file"
          accept=".iso"
          className="mb-3 w-full text-sm"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        {uploading && (
          <div className="mb-3 h-1.5 w-full overflow-hidden rounded-full bg-neutral-100">
            <div className="h-full bg-neutral-900 transition-all" style={{ width: `${progress}%` }} />
          </div>
        )}
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" onClick={onClose} disabled={uploading}>
            Cancel
          </button>
          <button type="submit" className="rounded-md bg-neutral-900 px-3 py-1.5 text-sm text-white disabled:opacity-50" disabled={uploading || !file}>
            {uploading ? `Uploading... ${progress}%` : "Upload"}
          </button>
        </div>
      </form>
    </div>
  );
}
