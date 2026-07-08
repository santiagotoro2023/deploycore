import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import Badge from "../components/Badge";
import DataTable from "../components/DataTable";
import { Deployment } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Deployments() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [deployments, setDeployments] = useState<Deployment[]>([]);

  useEffect(() => {
    if (!selectedOrgId) return;
    api.get<Deployment[]>(`/organizations/${selectedOrgId}/deployments`).then(setDeployments);
  }, [selectedOrgId]);

  const canDeploy = roleAtLeast(effectiveRole(selectedOrgId), "operator");

  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Deployments</h1>
        {canDeploy && (
          <Link
            to="/deployments/new"
            className="flex items-center gap-1.5 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800"
          >
            <Plus size={15} strokeWidth={2} />
            New deployment
          </Link>
        )}
      </div>

      <DataTable<Deployment>
        rows={deployments}
        rowKey={(d) => d.id}
        searchValue={(d) => d.hostname}
        columns={[
          {
            key: "hostname",
            header: "Hostname",
            render: (d) => (
              <Link to={`/deployments/${d.id}`} className="font-medium hover:underline">
                {d.hostname}
              </Link>
            ),
            sortValue: (d) => d.hostname,
          },
          { key: "state", header: "State", render: (d) => <Badge value={d.state} /> },
          { key: "ip_mode", header: "IP mode", render: (d) => d.ip_mode },
          {
            key: "created_at",
            header: "Created",
            render: (d) => new Date(d.created_at).toLocaleString(),
            sortValue: (d) => d.created_at,
          },
        ]}
      />
    </div>
  );
}
