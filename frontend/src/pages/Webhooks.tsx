import { Plug, Plus, Trash2 } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import Badge from "../components/Badge";
import ConfirmDialog from "../components/ConfirmDialog";
import DataTable from "../components/DataTable";
import { Webhook, WebhookDelivery, WEBHOOK_EVENT_TYPES } from "../api/types";
import { useAuth, roleAtLeast } from "../state/auth";
import { useOrg } from "../state/org";

export default function Webhooks() {
  const { selectedOrgId } = useOrg();
  const { effectiveRole } = useAuth();
  const [webhooks, setWebhooks] = useState<Webhook[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [deliveries, setDeliveries] = useState<WebhookDelivery[]>([]);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{ id: string; ok: boolean; message: string } | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  async function load() {
    if (!selectedOrgId) return;
    setWebhooks(await api.get<Webhook[]>(`/organizations/${selectedOrgId}/webhooks`));
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId]);

  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;
  const canManage = roleAtLeast(effectiveRole(selectedOrgId), "admin");

  async function toggleExpand(webhook: Webhook) {
    if (expanded === webhook.id) {
      setExpanded(null);
      return;
    }
    setExpanded(webhook.id);
    setDeliveries(await api.get<WebhookDelivery[]>(`/organizations/${selectedOrgId}/webhooks/${webhook.id}/deliveries`));
  }

  async function testWebhook(webhookId: string) {
    setTestingId(webhookId);
    setTestResult(null);
    try {
      const result = await api.post<{ ok: boolean; status_code: number | null; message: string }>(
        `/organizations/${selectedOrgId}/webhooks/${webhookId}/test`,
      );
      setTestResult({ id: webhookId, ok: result.ok, message: `${result.status_code ?? "no response"}: ${result.message}` });
    } catch (err) {
      setTestResult({ id: webhookId, ok: false, message: err instanceof ApiError ? err.message : "Test failed." });
    } finally {
      setTestingId(null);
    }
  }

  async function deleteWebhook() {
    if (!confirmDeleteId) return;
    await api.delete(`/organizations/${selectedOrgId}/webhooks/${confirmDeleteId}`);
    setConfirmDeleteId(null);
    await load();
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Webhooks</h1>
          <p className="text-xs text-neutral-500">
            Generic outbound webhooks for ticketing automation (Jira, ServiceNow, Zapier, n8n, etc.). Each
            delivery is signed with HMAC-SHA256 in an X-DeployCore-Signature header.
          </p>
        </div>
        {canManage && (
          <button
            className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-blue-700"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={15} strokeWidth={2} />
            New webhook
          </button>
        )}
      </div>

      <DataTable<Webhook>
        rows={webhooks}
        rowKey={(w) => w.id}
        searchValue={(w) => w.name}
        columns={[
          { key: "name", header: "Name", render: (w) => w.name, sortValue: (w) => w.name },
          { key: "url", header: "URL", render: (w) => w.url },
          { key: "events", header: "Events", render: (w) => w.events.join(", ") },
          { key: "enabled", header: "Status", render: (w) => <Badge value={w.enabled ? "active" : "unknown"} /> },
          {
            key: "actions",
            header: "",
            render: (w) =>
              canManage && (
                <div className="flex items-center gap-2">
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 px-2 py-1 text-xs hover:bg-neutral-50 disabled:opacity-50"
                    disabled={testingId === w.id}
                    onClick={() => testWebhook(w.id)}
                  >
                    <Plug size={13} strokeWidth={1.75} />
                    {testingId === w.id ? "Testing..." : "Test"}
                  </button>
                  <button className="text-xs text-neutral-500 hover:text-neutral-900" onClick={() => toggleExpand(w)}>
                    {expanded === w.id ? "Hide deliveries" : "Deliveries"}
                  </button>
                  <button
                    className="flex items-center gap-1 rounded-md border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
                    onClick={() => setConfirmDeleteId(w.id)}
                  >
                    <Trash2 size={13} strokeWidth={1.75} />
                  </button>
                </div>
              ),
          },
        ]}
      />

      {testResult && (
        <div className={`rounded-md border p-3 text-sm ${testResult.ok ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-red-200 bg-red-50 text-red-700"}`}>
          {testResult.message}
        </div>
      )}

      {expanded && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-neutral-700">Recent deliveries</h2>
          <div className="divide-y divide-neutral-100 rounded-lg border border-neutral-200 bg-white text-sm">
            {deliveries.length === 0 && <div className="p-4 text-neutral-400">No deliveries yet.</div>}
            {deliveries.map((d, i) => (
              <div key={i} className="flex items-center justify-between px-4 py-2.5">
                <span>{d.event_type}</span>
                <Badge value={d.success ? "ok" : "failed"} />
                <span className="text-xs text-neutral-400">{d.status_code ?? "no response"}</span>
                <span className="text-xs text-neutral-400">{new Date(d.occurred_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {showCreate && (
        <CreateWebhookForm
          orgId={selectedOrgId}
          onClose={() => setShowCreate(false)}
          onCreated={async () => {
            setShowCreate(false);
            await load();
          }}
        />
      )}

      <ConfirmDialog
        open={!!confirmDeleteId}
        title="Delete webhook"
        message="This stops all future deliveries for this webhook. Past delivery history is removed too. This cannot be undone."
        confirmLabel="Delete"
        onConfirm={deleteWebhook}
        onCancel={() => setConfirmDeleteId(null)}
      />
    </div>
  );
}

function CreateWebhookForm({
  orgId,
  onClose,
  onCreated,
}: {
  orgId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [events, setEvents] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  function toggleEvent(event: string) {
    setEvents((prev) => (prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]));
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.post(`/organizations/${orgId}/webhooks`, { name, url, secret, enabled: true, events });
      onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create webhook.");
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <form onSubmit={onSubmit} className="w-96 rounded-lg border border-neutral-200 bg-white p-5 shadow-sm">
        <h2 className="mb-4 text-sm font-semibold">New webhook</h2>
        <label className="mb-1 block text-xs font-medium text-neutral-600">Name</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={name} onChange={(e) => setName(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">URL</label>
        <input required type="url" className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={url} onChange={(e) => setUrl(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Signing secret</label>
        <input required className="mb-3 w-full rounded-md border border-neutral-300 px-3 py-1.5 text-sm" value={secret} onChange={(e) => setSecret(e.target.value)} />
        <label className="mb-1 block text-xs font-medium text-neutral-600">Events</label>
        <div className="mb-3 space-y-1">
          {WEBHOOK_EVENT_TYPES.map((event) => (
            <label key={event} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={events.includes(event)} onChange={() => toggleEvent(event)} />
              {event}
            </label>
          ))}
        </div>
        {error && <div className="mb-3 text-xs text-red-600">{error}</div>}
        <div className="flex justify-end gap-2">
          <button type="button" className="rounded-md border border-neutral-300 px-3 py-1.5 text-sm" onClick={onClose}>Cancel</button>
          <button type="submit" className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white">Create</button>
        </div>
      </form>
    </div>
  );
}
