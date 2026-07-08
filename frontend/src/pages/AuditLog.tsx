import { useEffect, useState } from "react";
import { api } from "../api/client";
import DataTable from "../components/DataTable";
import { useOrg } from "../state/org";

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

  useEffect(() => {
    if (!selectedOrgId) return;
    api.get<AuditLogEntry[]>(`/organizations/${selectedOrgId}/audit-log`).then(setEntries);
  }, [selectedOrgId]);

  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Audit Log</h1>
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
          { key: "detail", header: "Detail", render: (e) => (e.detail ? JSON.stringify(e.detail) : "—") },
        ]}
      />
    </div>
  );
}
