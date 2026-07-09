import { Download, Power, PowerOff, RotateCw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError, getToken } from "../api/client";
import Badge from "../components/Badge";
import ConfirmDialog from "../components/ConfirmDialog";
import { downloadText } from "../lib/jsonFile";
import { Deployment, DeploymentHealthCheck, DeploymentLogLine, DeploymentStateTransition } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

const STAGES = ["pending", "creating_vm", "booting", "installing_os", "post_install", "configuring", "completed"];
const TERMINAL_STATES = new Set(["completed", "failed"]);

export default function DeploymentDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [deployment, setDeployment] = useState<Deployment | null>(null);
  const [history, setHistory] = useState<DeploymentStateTransition[]>([]);
  const [logs, setLogs] = useState<DeploymentLogLine[]>([]);
  const [healthHistory, setHealthHistory] = useState<DeploymentHealthCheck[]>([]);
  const [confirmRetry, setConfirmRetry] = useState(false);
  const [confirmDeleteVm, setConfirmDeleteVm] = useState(false);
  const [confirmDeleteDeployment, setConfirmDeleteDeployment] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [powerState, setPowerState] = useState<string | null>(null);
  const [powerBusy, setPowerBusy] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  async function loadPowerState(orgId: string, deploymentId: string) {
    const { power_state } = await api.get<{ power_state: string | null }>(
      `/organizations/${orgId}/deployments/${deploymentId}/power`,
    );
    setPowerState(power_state);
  }

  async function loadStatic() {
    if (!selectedOrgId || !id) return;
    const [dep, hist, logLines] = await Promise.all([
      api.get<Deployment>(`/organizations/${selectedOrgId}/deployments/${id}`),
      api.get<DeploymentStateTransition[]>(`/organizations/${selectedOrgId}/deployments/${id}/history`),
      api.get<DeploymentLogLine[]>(`/organizations/${selectedOrgId}/deployments/${id}/logs`),
    ]);
    setDeployment(dep);
    setHistory(hist);
    setLogs(logLines);
    if (dep.vm_moref) await loadPowerState(selectedOrgId, id);
    if (dep.state === "completed") {
      const checks = await api.get<DeploymentHealthCheck[]>(
        `/organizations/${selectedOrgId}/deployments/${id}/health-history`,
      );
      setHealthHistory(checks);
    }
  }

  useEffect(() => {
    loadStatic();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId, id]);

  useEffect(() => {
    if (!selectedOrgId || !id) return;
    const controller = new AbortController();
    controllerRef.current = controller;

    async function streamEvents() {
      const res = await fetch(`/api/organizations/${selectedOrgId}/deployments/${id}/events`, {
        headers: { Authorization: `Bearer ${getToken()}` },
        signal: controller.signal,
      });
      if (!res.body) return;
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const eventLine = block.split("\n").find((l) => l.startsWith("event:"));
          const dataLine = block.split("\n").find((l) => l.startsWith("data:"));
          if (!dataLine) continue;
          const payload = JSON.parse(dataLine.slice(5).trim());
          const kind = eventLine?.slice(6).trim();
          if (kind === "log") {
            setLogs((prev) => [...prev, payload]);
          } else if (kind === "transition") {
            setHistory((prev) => [...prev, payload]);
            setDeployment((prev) => (prev ? { ...prev, state: payload.to_state } : prev));
          }
        }
      }
    }

    streamEvents().catch(() => undefined);
    return () => controller.abort();
  }, [selectedOrgId, id]);

  if (!deployment) return <p className="text-sm text-neutral-500">Loading...</p>;

  const canRetry = deployment.state === "failed" && roleAtLeast(effectiveRole(selectedOrgId), "operator");
  const canOperateVm = roleAtLeast(effectiveRole(selectedOrgId), "operator");
  const canDeleteVm = roleAtLeast(effectiveRole(selectedOrgId), "admin");
  const canDeleteDeployment = canDeleteVm && TERMINAL_STATES.has(deployment.state);
  const currentStageIndex = STAGES.indexOf(deployment.state);

  async function retry() {
    if (!selectedOrgId || !id) return;
    await api.post(`/organizations/${selectedOrgId}/deployments/${id}/retry`);
    setConfirmRetry(false);
    await loadStatic();
  }

  async function powerOn() {
    if (!selectedOrgId || !id) return;
    setPowerBusy(true);
    try {
      const { power_state } = await api.post<{ power_state: string }>(
        `/organizations/${selectedOrgId}/deployments/${id}/power/on`,
      );
      setPowerState(power_state);
    } finally {
      setPowerBusy(false);
    }
  }

  async function powerOff(hard: boolean) {
    if (!selectedOrgId || !id) return;
    setPowerBusy(true);
    try {
      const { power_state } = await api.post<{ power_state: string }>(
        `/organizations/${selectedOrgId}/deployments/${id}/power/off`,
        { hard },
      );
      setPowerState(power_state);
    } finally {
      setPowerBusy(false);
    }
  }

  async function deleteVm() {
    if (!selectedOrgId || !id) return;
    await api.delete(`/organizations/${selectedOrgId}/deployments/${id}/vm`);
    setConfirmDeleteVm(false);
    setPowerState(null);
    await loadStatic();
  }

  async function deleteDeployment() {
    if (!selectedOrgId || !id) return;
    setDeleteError(null);
    try {
      await api.delete(`/organizations/${selectedOrgId}/deployments/${id}`);
      navigate("/deployments");
    } catch (err) {
      setConfirmDeleteDeployment(false);
      setDeleteError(err instanceof ApiError ? err.message : "Failed to delete deployment.");
    }
  }

  function downloadFullLog() {
    if (!deployment) return;
    const lines: string[] = [];
    lines.push(`Deployment ${deployment.hostname} (${deployment.id})`);
    lines.push(`State: ${deployment.state}`);
    if (deployment.error_message) lines.push(`Error: ${deployment.error_message}`);
    lines.push("");
    lines.push("=== State history ===");
    for (const t of history) {
      lines.push(`${t.occurred_at}  ${t.from_state} -> ${t.to_state}${t.detail ? "  (" + t.detail + ")" : ""}`);
    }
    lines.push("");
    lines.push("=== Log ===");
    for (const line of logs) {
      lines.push(`${line.ts}  [${line.stage}] [${line.level}]  ${line.message}`);
    }
    downloadText(`deployment-${deployment.hostname}-log.txt`, lines.join("\n"));
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">{deployment.hostname}</h1>
          <p className="text-xs text-neutral-500">{deployment.id}</p>
        </div>
        <div className="flex items-center gap-3">
          {deployment.state === "completed" && (
            <span className="flex items-center gap-1.5 text-xs text-neutral-500">
              Health:
              <Badge value={deployment.last_health_status} />
              {deployment.last_health_checked_at && (
                <span>checked {new Date(deployment.last_health_checked_at).toLocaleString()}</span>
              )}
            </span>
          )}
          <Badge value={deployment.state} />
          {canRetry && (
            <button
              className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
              onClick={() => setConfirmRetry(true)}
            >
              <RotateCw size={14} strokeWidth={1.75} />
              Retry
            </button>
          )}
          {canDeleteDeployment && (
            <button
              className="flex items-center gap-1.5 rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
              onClick={() => setConfirmDeleteDeployment(true)}
            >
              <Trash2 size={14} strokeWidth={1.75} />
              Delete deployment
            </button>
          )}
        </div>
      </div>

      {deleteError && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {deleteError}
        </div>
      )}

      {deployment.state === "completed" && healthHistory.length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-neutral-700 dark:text-neutral-300">Health history</h2>
          <div className="flex flex-wrap items-center gap-1.5 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-4">
            {[...healthHistory].reverse().map((check, i) => (
              <span key={i} title={`${check.status} at ${new Date(check.checked_at).toLocaleString()}`}>
                <Badge value={check.status} />
              </span>
            ))}
          </div>
        </div>
      )}

      {deployment.error_message && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">
          {deployment.error_message}
        </div>
      )}

      {deployment.vm_moref && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-neutral-700 dark:text-neutral-300">Virtual machine</h2>
          <div className="flex items-center justify-between rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-4">
            <div className="flex items-center gap-3 text-sm">
              <span className="text-neutral-500">Power state:</span>
              {powerState ? <Badge value={powerState === "poweredOn" ? "ok" : powerState} /> : <span className="text-neutral-400">unknown</span>}
            </div>
            {canOperateVm && (
              <div className="flex items-center gap-2">
                <button
                  className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50"
                  disabled={powerBusy || powerState === "poweredOn"}
                  onClick={powerOn}
                >
                  <Power size={14} strokeWidth={1.75} />
                  Power on
                </button>
                <button
                  className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50"
                  disabled={powerBusy || powerState === "poweredOff"}
                  onClick={() => powerOff(false)}
                >
                  <PowerOff size={14} strokeWidth={1.75} />
                  Shut down
                </button>
                <button
                  className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-50"
                  disabled={powerBusy || powerState === "poweredOff"}
                  onClick={() => powerOff(true)}
                >
                  <PowerOff size={14} strokeWidth={1.75} />
                  Power off
                </button>
                {canDeleteVm && (
                  <button
                    className="flex items-center gap-1.5 rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950"
                    onClick={() => setConfirmDeleteVm(true)}
                  >
                    <Trash2 size={14} strokeWidth={1.75} />
                    Delete VM
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <div>
        <h2 className="mb-2 text-sm font-semibold text-neutral-700 dark:text-neutral-300">Pipeline</h2>
        <div className="flex items-center gap-1 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-4">
          {STAGES.map((stage, i) => (
            <div key={stage} className="flex flex-1 items-center">
              <div
                className={`flex h-6 flex-1 items-center justify-center rounded-md text-xs font-medium ${
                  deployment.state === "failed" && i > currentStageIndex
                    ? "bg-neutral-100 text-neutral-400 dark:bg-neutral-800 dark:text-neutral-500"
                    : i < currentStageIndex || deployment.state === "completed"
                      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-400"
                      : i === currentStageIndex
                        ? "bg-blue-600 text-white"
                        : "bg-neutral-100 text-neutral-400 dark:bg-neutral-800 dark:text-neutral-500"
                }`}
              >
                {stage.replace(/_/g, " ")}
              </div>
              {i < STAGES.length - 1 && <div className="h-px w-2 bg-neutral-200 dark:bg-neutral-700" />}
            </div>
          ))}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold text-neutral-700 dark:text-neutral-300">State history</h2>
        <div className="divide-y divide-neutral-100 dark:divide-neutral-800 rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 text-sm">
          {history.map((t, i) => (
            <div key={i} className="flex items-center justify-between px-4 py-2">
              <span>
                {t.from_state} → {t.to_state}
                {t.detail && <span className="ml-2 text-neutral-400">({t.detail})</span>}
              </span>
              <span className="text-xs text-neutral-400">{new Date(t.occurred_at).toLocaleTimeString()}</span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-700 dark:text-neutral-300">Log</h2>
          <button
            className="flex items-center gap-1 text-xs text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
            onClick={downloadFullLog}
          >
            <Download size={13} strokeWidth={1.75} />
            Download full log
          </button>
        </div>
        <div className="max-h-96 overflow-y-auto rounded-lg border border-neutral-200 bg-neutral-950 p-4 font-mono text-xs text-neutral-200">
          {logs.length === 0 && <div className="text-neutral-500">No log output yet.</div>}
          {logs.map((line, i) => (
            <div key={i} className={line.level === "error" ? "text-red-400" : line.level === "warn" ? "text-amber-300" : ""}>
              <span className="text-neutral-500">{new Date(line.ts).toLocaleTimeString()}</span>{" "}
              <span className="text-neutral-500">[{line.stage}]</span> {line.message}
            </div>
          ))}
        </div>
      </div>

      <ConfirmDialog
        open={confirmRetry}
        title="Retry deployment"
        message="This restarts provisioning from the beginning. Continue?"
        confirmLabel="Retry"
        onConfirm={retry}
        onCancel={() => setConfirmRetry(false)}
      />
      <ConfirmDialog
        open={confirmDeleteVm}
        title="Delete virtual machine"
        message="This permanently deletes the VM from the hypervisor. The deployment record and its history are kept. This cannot be undone."
        confirmLabel="Delete VM"
        onConfirm={deleteVm}
        onCancel={() => setConfirmDeleteVm(false)}
      />
      <ConfirmDialog
        open={confirmDeleteDeployment}
        title="Delete deployment"
        message={
          deployment.vm_moref
            ? "This deployment still has a VM. Delete the VM first, then come back to delete the deployment."
            : "Removes this deployment from lists and the dashboard. Its log history is kept, not shown anywhere in the UI, but not erased either. This cannot be undone from here."
        }
        confirmLabel="Delete deployment"
        onConfirm={deleteDeployment}
        onCancel={() => setConfirmDeleteDeployment(false)}
      />
    </div>
  );
}
