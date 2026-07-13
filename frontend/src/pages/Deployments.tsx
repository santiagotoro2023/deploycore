import { Download, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import Badge from "../components/Badge";
import DataTable from "../components/DataTable";
import Pagination from "../components/Pagination";
import Select from "../components/Select";
import { downloadCsv } from "../lib/jsonFile";
import { Deployment, DeploymentState } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

const PAGE_SIZE = 50;
const STATE_OPTIONS: DeploymentState[] = [
  "pending",
  "creating_vm",
  "booting",
  "installing_os",
  "post_install",
  "configuring",
  "completed",
  "failed",
];

export default function Deployments() {
  const { selectedOrgId, loaded: orgLoaded } = useOrg();
  const { effectiveRole } = useAuth();
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [stateFilter, setStateFilter] = useState<DeploymentState | "">("");
  const [hostnameFilter, setHostnameFilter] = useState("");
  const [offset, setOffset] = useState(0);

  async function load() {
    if (!selectedOrgId) return;
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
      if (stateFilter) params.set("state", stateFilter);
      if (hostnameFilter) params.set("q", hostnameFilter);
      setDeployments(await api.get<Deployment[]>(`/organizations/${selectedOrgId}/deployments?${params}`));
    } finally {
      setLoaded(true);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId, stateFilter, hostnameFilter, offset]);

  useEffect(() => {
    setOffset(0);
  }, [stateFilter, hostnameFilter]);

  const canDeploy = roleAtLeast(effectiveRole(selectedOrgId), "operator");

  if (!orgLoaded) return null;
  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;

  async function exportCsv() {
    if (!selectedOrgId) return;
    const params = new URLSearchParams({ limit: "5000", offset: "0" });
    if (stateFilter) params.set("state", stateFilter);
    if (hostnameFilter) params.set("q", hostnameFilter);
    const all = await api.get<Deployment[]>(`/organizations/${selectedOrgId}/deployments?${params}`);
    downloadCsv(
      "deployments.csv",
      all.map((d) => ({
        hostname: d.hostname,
        state: d.state,
        ip_mode: d.ip_mode,
        created_at: d.created_at,
      })),
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Deployments</h1>
        <div className="flex items-center gap-2">
          <button
            className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
            onClick={exportCsv}
          >
            <Download size={15} strokeWidth={1.75} />
            Export CSV
          </button>
          {canDeploy && (
            <Link
              to="/deployments/new"
              className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            >
              <Plus size={15} strokeWidth={2} />
              New deployment
            </Link>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input
          placeholder="Search hostname..."
          className="w-56 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={hostnameFilter}
          onChange={(e) => setHostnameFilter(e.target.value)}
        />
        <Select
          className="w-48 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
          value={stateFilter}
          onChange={(e) => setStateFilter(e.target.value as DeploymentState | "")}
        >
          <option value="">All states</option>
          {STATE_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s.replace(/_/g, " ")}
            </option>
          ))}
        </Select>
      </div>

      <DataTable<Deployment>
        rows={deployments}
        loading={!loaded}
        rowKey={(d) => d.id}
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
          { key: "state", header: "State", render: (d) => <Badge value={d.state} />, shrink: true },
          { key: "ip_mode", header: "IP mode", render: (d) => d.ip_mode },
          {
            key: "created_at",
            header: "Created",
            render: (d) => new Date(d.created_at).toLocaleString(),
            sortValue: (d) => d.created_at,
          },
        ]}
      />

      <Pagination offset={offset} limit={PAGE_SIZE} count={deployments.length} onOffsetChange={setOffset} />
    </div>
  );
}
