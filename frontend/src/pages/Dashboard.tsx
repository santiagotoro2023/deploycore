import { Activity, CheckCircle2, Circle, Server, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import AmbientBackground from "../components/AmbientBackground";
import Badge from "../components/Badge";
import { Deployment, DeploymentTemplate, HypervisorHost, IsoAsset } from "../api/types";
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
    <div className="relative -m-8 overflow-hidden p-8">
      <AmbientBackground subtle />
      <div className="relative space-y-8">
        <h1 className="text-lg font-semibold">Dashboard</h1>
        {isGlobalAdmin && <MspOverview />}
        {selectedOrgId ? (
          <OrgDashboard orgId={selectedOrgId} />
        ) : (
          !isGlobalAdmin && <p className="text-sm text-neutral-500">Select an organization to view its dashboard.</p>
        )}
      </div>
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
      <h2 className="mb-2 text-sm font-semibold text-neutral-700 dark:text-neutral-300">All organizations</h2>
      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-xs text-neutral-500 dark:border-neutral-700">
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
                className="cursor-pointer border-b border-neutral-100 last:border-0 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800"
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
  const [isoAssets, setIsoAssets] = useState<IsoAsset[]>([]);
  const [templates, setTemplates] = useState<DeploymentTemplate[]>([]);

  useEffect(() => {
    api.get<Deployment[]>(`/organizations/${orgId}/deployments`).then(setDeployments);
    api.get<HypervisorHost[]>(`/organizations/${orgId}/hypervisors`).then(setHosts);
    api.get<IsoAsset[]>(`/organizations/${orgId}/iso-assets`).then(setIsoAssets);
    api.get<DeploymentTemplate[]>(`/organizations/${orgId}/templates`).then(setTemplates);
  }, [orgId]);

  const running = deployments.filter((d) => RUNNING_STATES.has(d.state)).length;
  const failed = deployments.filter((d) => d.state === "failed").length;
  const completed = deployments.filter((d) => d.state === "completed").length;
  const recent = [...deployments].sort((a, b) => (a.created_at < b.created_at ? 1 : -1)).slice(0, 8);

  const hasHypervisor = hosts.length > 0;
  const hasWindowsIso = isoAssets.some((i) => i.kind === "windows_iso");
  const hasTemplate = templates.length > 0;

  return (
    <div className="space-y-8">
      {!(hasHypervisor && hasWindowsIso && hasTemplate) && (
        <GettingStartedCard hasHypervisor={hasHypervisor} hasWindowsIso={hasWindowsIso} hasTemplate={hasTemplate} />
      )}

      <div className="grid grid-cols-4 gap-4">
        <StatTile icon={Activity} color="text-blue-500" label="Running" value={running} />
        <StatTile icon={CheckCircle2} color="text-emerald-500" label="Completed" value={completed} />
        <StatTile icon={XCircle} color="text-red-500" label="Failed" value={failed} />
        <StatTile
          icon={Server}
          color="text-violet-500"
          label="Hypervisors OK"
          value={`${hosts.filter((h) => h.last_test_status === "ok").length} / ${hosts.length}`}
        />
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-neutral-700 dark:text-neutral-300">Recent deployments</h2>
        <div className="divide-y divide-neutral-100 dark:divide-neutral-800 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900">
          {recent.length === 0 && <div className="p-4 text-sm text-neutral-400">No deployments yet.</div>}
          {recent.map((d) => (
            <Link
              key={d.id}
              to={`/deployments/${d.id}`}
              className="flex items-center justify-between px-4 py-2.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
            >
              <span className="font-medium">{d.hostname}</span>
              <Badge value={d.state} />
            </Link>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-neutral-700 dark:text-neutral-300">Hypervisor connection health</h2>
        <div className="divide-y divide-neutral-100 dark:divide-neutral-800 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900">
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

function GettingStartedCard({
  hasHypervisor,
  hasWindowsIso,
  hasTemplate,
}: {
  hasHypervisor: boolean;
  hasWindowsIso: boolean;
  hasTemplate: boolean;
}) {
  const steps = [
    { done: hasHypervisor, label: "Add a hypervisor", to: "/hypervisors" },
    { done: hasWindowsIso, label: "Upload a Windows Server ISO", to: "/iso-assets" },
    { done: hasTemplate, label: "Create a deployment template", to: "/templates" },
  ];

  return (
    <div className="rounded-lg border border-blue-200 bg-blue-50 p-5 dark:border-blue-900 dark:bg-blue-950">
      <h2 className="mb-1 text-sm font-semibold text-blue-900 dark:text-blue-300">Getting started</h2>
      <p className="mb-3 text-xs text-blue-800 dark:text-blue-400">
        A few steps before you can deploy your first Windows Server:
      </p>
      <div className="space-y-1.5">
        {steps.map((step) => (
          <Link
            key={step.to}
            to={step.to}
            className="flex items-center gap-2 text-sm text-blue-900 hover:underline dark:text-blue-300"
          >
            {step.done ? (
              <CheckCircle2 size={16} strokeWidth={1.75} className="text-emerald-600" />
            ) : (
              <Circle size={16} strokeWidth={1.75} className="text-blue-400" />
            )}
            <span className={step.done ? "text-blue-700 line-through" : ""}>{step.label}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function StatTile({
  icon: Icon,
  color,
  label,
  value,
}: {
  icon: typeof Activity;
  color: string;
  label: string;
  value: number | string;
}) {
  return (
    <div className="flex items-start justify-between rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-4">
      <div>
        <div className="text-xs text-neutral-500">{label}</div>
        <div className="mt-1 text-2xl font-semibold">{value}</div>
      </div>
      <Icon size={18} strokeWidth={1.75} className={color} />
    </div>
  );
}
