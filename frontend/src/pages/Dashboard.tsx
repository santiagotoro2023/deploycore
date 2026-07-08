import { Activity, CheckCircle2, Server, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import Badge from "../components/Badge";
import { Deployment, HypervisorHost } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

const RUNNING_STATES = new Set(["pending", "creating_vm", "booting", "installing_os", "post_install", "configuring"]);

interface OrgOverview {
  org_id: string;
  org_name: string;
  running: number;
  completed: number;
  failed: number;
  hypervisors_ok: number;
  hypervisors_total: number;
}

export default function Dashboard() {
  const { user } = useAuth();
  const { selectedOrgId } = useOrg();
  const isGlobalAdmin = !!user && roleAtLeast(user.global_role, "admin");

  return (
    <div className="space-y-8">
      <h1 className="text-lg font-semibold">Dashboard</h1>
      {isGlobalAdmin && <MspOverview />}
      {selectedOrgId ? (
        <OrgDashboard orgId={selectedOrgId} />
      ) : (
        !isGlobalAdmin && <p className="text-sm text-neutral-500">Select an organization to view its dashboard.</p>
      )}
    </div>
  );
}

function MspOverview() {
  const { selectOrg } = useOrg();
  const [rows, setRows] = useState<OrgOverview[]>([]);

  useEffect(() => {
    api.get<OrgOverview[]>("/dashboard/overview").then(setRows);
  }, []);

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-neutral-700">All organizations</h2>
      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-xs text-neutral-500">
              <th className="px-4 py-2 font-medium">Organization</th>
              <th className="px-4 py-2 font-medium">Running</th>
              <th className="px-4 py-2 font-medium">Completed</th>
              <th className="px-4 py-2 font-medium">Failed</th>
              <th className="px-4 py-2 font-medium">Hypervisors</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td className="px-4 py-6 text-center text-neutral-400" colSpan={5}>
                  No organizations yet.
                </td>
              </tr>
            )}
            {rows.map((r) => (
              <tr
                key={r.org_id}
                className="cursor-pointer border-b border-neutral-100 last:border-0 hover:bg-neutral-50"
                onClick={() => selectOrg(r.org_id)}
              >
                <td className="px-4 py-2 font-medium">{r.org_name}</td>
                <td className="px-4 py-2">{r.running}</td>
                <td className="px-4 py-2">{r.completed}</td>
                <td className="px-4 py-2">{r.failed > 0 ? <span className="text-red-600">{r.failed}</span> : r.failed}</td>
                <td className="px-4 py-2">
                  {r.hypervisors_ok} / {r.hypervisors_total}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OrgDashboard({ orgId }: { orgId: string }) {
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [hosts, setHosts] = useState<HypervisorHost[]>([]);

  useEffect(() => {
    api.get<Deployment[]>(`/organizations/${orgId}/deployments`).then(setDeployments);
    api.get<HypervisorHost[]>(`/organizations/${orgId}/hypervisors`).then(setHosts);
  }, [orgId]);

  const running = deployments.filter((d) => RUNNING_STATES.has(d.state)).length;
  const failed = deployments.filter((d) => d.state === "failed").length;
  const completed = deployments.filter((d) => d.state === "completed").length;
  const recent = [...deployments].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, 8);

  return (
    <div className="space-y-8">
      <div className="grid grid-cols-4 gap-4">
        <StatTile icon={Activity} label="Running" value={running} />
        <StatTile icon={CheckCircle2} label="Completed" value={completed} />
        <StatTile icon={XCircle} label="Failed" value={failed} />
        <StatTile
          icon={Server}
          label="Hypervisors OK"
          value={`${hosts.filter((h) => h.last_test_status === "ok").length} / ${hosts.length}`}
        />
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-neutral-700">Recent deployments</h2>
        <div className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 bg-white">
          {recent.length === 0 && <div className="p-4 text-sm text-neutral-400">No deployments yet.</div>}
          {recent.map((d) => (
            <Link
              key={d.id}
              to={`/deployments/${d.id}`}
              className="flex items-center justify-between px-4 py-2.5 text-sm hover:bg-neutral-50"
            >
              <span className="font-medium">{d.hostname}</span>
              <Badge value={d.state} />
            </Link>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-neutral-700">Hypervisor connection health</h2>
        <div className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 bg-white">
          {hosts.length === 0 && <div className="p-4 text-sm text-neutral-400">No hypervisors registered.</div>}
          {hosts.map((h) => (
            <div key={h.id} className="flex items-center justify-between px-4 py-2.5 text-sm">
              <span>{h.name}</span>
              <Badge value={h.last_test_status} />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Activity;
  label: string;
  value: number | string;
}) {
  return (
    <div className="flex items-start justify-between rounded-lg border border-neutral-200 bg-white p-4">
      <div>
        <div className="text-xs text-neutral-500">{label}</div>
        <div className="mt-1 text-2xl font-semibold">{value}</div>
      </div>
      <Icon size={18} strokeWidth={1.75} className="text-neutral-300" />
    </div>
  );
}
