import { Download } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import DataTable from "../components/DataTable";
import Pagination from "../components/Pagination";
import { downloadCsv } from "../lib/jsonFile";
import { useOrg } from "../state/org";

const PAGE_SIZE = 50;

interface AuditLogEntry {
  id: string;
  user_id: string | null;
  action: string;
  target_type: string;
  target_id: string | null;
  detail: Record<string, unknown> | null;
  occurred_at: string;
}

export default function AuditLog() {
  const { selectedOrgId } = useOrg();
  const [entries, setEntries] = useState<AuditLogEntry[]>([]);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    if (!selectedOrgId) return;
    api
      .get<AuditLogEntry[]>(`/organizations/${selectedOrgId}/audit-log?limit=${PAGE_SIZE}&offset=${offset}`)
      .then(setEntries);
  }, [selectedOrgId, offset]);

  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;

  async function exportCsv() {
    if (!selectedOrgId) return;
    const all = await api.get<AuditLogEntry[]>(`/organizations/${selectedOrgId}/audit-log?limit=5000&offset=0`);
    downloadCsv(
      "audit-log.csv",
      all.map((e) => ({
        occurred_at: e.occurred_at,
        action: e.action,
        target_type: e.target_type,
        target_id: e.target_id ?? "",
        detail: e.detail ? JSON.stringify(e.detail) : "",
      })),
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Audit Log</h1>
        <button
          className="flex items-center gap-1.5 rounded-md border border-neutral-300 px-3 py-1.5 text-sm hover:bg-neutral-50"
          onClick={exportCsv}
        >
          <Download size={15} strokeWidth={1.75} />
          Export CSV
        </button>
      </div>
      <DataTable<AuditLogEntry>
        rows={entries}
        rowKey={(e) => e.id}
        searchValue={(e) => e.action}
        columns={[
          {
            key: "occurred_at",
            header: "Time",
            render: (e) => new Date(e.occurred_at).toLocaleString(),
            sortValue: (e) => e.occurred_at,
          },
          { key: "action", header: "Action", render: (e) => e.action },
          { key: "target_type", header: "Target", render: (e) => e.target_type },
          { key: "detail", header: "Detail", render: (e) => (e.detail ? JSON.stringify(e.detail) : "(none)") },
        ]}
      />
      <Pagination offset={offset} limit={PAGE_SIZE} count={entries.length} onOffsetChange={setOffset} />
    </div>
  );
}
