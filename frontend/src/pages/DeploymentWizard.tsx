import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import Select from "../components/Select";
import TemplateFieldsForm, { TemplateFieldsBody } from "../components/TemplateFieldsForm";
import { AppAsset, Deployment, DeploymentTemplate, DiskLayout, HypervisorHost, IpMode, IsoAsset } from "../api/types";
import { useOrg } from "../state/org";

// "Leave blank to keep unchanged" (see TemplateFieldsForm) doesn't
// translate to an actual override here - an empty string would
// overwrite the real, encrypted template secret with a blank one the
// moment it's included in overrides at all, since the backend can't
// distinguish "deliberately blank" from "not touched" once it's in the
// dict. Stripped before overrides are ever sent, same rule the
// template-edit PATCH route already enforces server-side.
const BLANK_MEANS_UNCHANGED_FIELDS = ["local_admin_password", "domain_join_credential"] as const;

function cleanOverrides(body: TemplateFieldsBody): Record<string, unknown> {
  const overrides: Record<string, unknown> = { ...body };
  for (const field of BLANK_MEANS_UNCHANGED_FIELDS) {
    if (!overrides[field]) delete overrides[field];
  }
  delete overrides.name;
  return overrides;
}

const STEPS = ["Template", "Hypervisor", "Hostname & network", "Review", "Deploy"];

// Windows Setup's specialize pass sets ComputerName as a NetBIOS name,
// hard capped at 15 characters (not the 63-character DNS hostname limit):
// go over it and Setup doesn't truncate, it fails to process the answer
// file partway through installation with a generic, unhelpful error, well
// after the VM's already been created. The backend enforces this too
// (schemas/deployment.py), this is just for immediate feedback instead of
// a round trip. Bulk mode appends a 2-digit suffix (01-50), so the prefix
// itself only has 13 characters to work with.
const COMPUTERNAME_MAX_LENGTH = 15;
const COMPUTERNAME_INVALID_CHARS = new Set("{|}~[\\]^':;<=>? ".split(""));

function computerNameError(value: string, label: string, maxLength: number): string | null {
  if (!value.trim()) return null; // handled by the button's own disabled state, not worth a separate message
  if (value.length > maxLength) {
    return `${label} is ${value.length} characters, Windows computer names allow at most ${maxLength}${
      maxLength < COMPUTERNAME_MAX_LENGTH ? " here (the 2-digit suffix takes the rest of the 15-character limit)" : ""
    }.`;
  }
  const bad = [...new Set([...value].filter((c) => COMPUTERNAME_INVALID_CHARS.has(c)))];
  if (bad.length > 0) return `${label} can't contain: ${bad.join(" ")}`;
  return null;
}

export default function DeploymentWizard() {
  const { selectedOrgId, loaded: orgLoaded } = useOrg();
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [templates, setTemplates] = useState<DeploymentTemplate[]>([]);
  const [hosts, setHosts] = useState<HypervisorHost[]>([]);
  const [diskLayouts, setDiskLayouts] = useState<DiskLayout[]>([]);
  const [isoAssets, setIsoAssets] = useState<IsoAsset[]>([]);
  const [appAssets, setAppAssets] = useState<AppAsset[]>([]);
  const [showCustomize, setShowCustomize] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, unknown> | null>(null);

  const [templateId, setTemplateId] = useState("");
  const [hypervisorHostId, setHypervisorHostId] = useState("");
  const [hostname, setHostname] = useState("");
  const [ipMode, setIpMode] = useState<IpMode>("dhcp");
  const [staticIp, setStaticIp] = useState("");
  const [staticNetmask, setStaticNetmask] = useState("");
  const [staticGateway, setStaticGateway] = useState("");
  const [staticDns, setStaticDns] = useState("");
  const [bulk, setBulk] = useState(false);
  const [bulkCount, setBulkCount] = useState(2);

  const [previewXml, setPreviewXml] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!selectedOrgId) return;
    api.get<DeploymentTemplate[]>(`/organizations/${selectedOrgId}/templates`).then(setTemplates);
    api.get<HypervisorHost[]>(`/organizations/${selectedOrgId}/hypervisors`).then(setHosts);
    api.get<DiskLayout[]>(`/organizations/${selectedOrgId}/disk-layouts`).then(setDiskLayouts);
    api.get<IsoAsset[]>(`/organizations/${selectedOrgId}/iso-assets`).then((rows) =>
      setIsoAssets(rows.filter((iso) => iso.kind === "windows_iso")),
    );
    api.get<AppAsset[]>(`/organizations/${selectedOrgId}/app-assets`).then(setAppAssets);
  }, [selectedOrgId]);

  if (!orgLoaded) return null;
  if (!selectedOrgId) return <p className="text-sm text-neutral-500">Select an organization first.</p>;

  const selectedTemplate = templates.find((t) => t.id === templateId);

  const hostnameError = bulk
    ? computerNameError(hostname, "Hostname prefix", COMPUTERNAME_MAX_LENGTH - 2)
    : computerNameError(hostname, "Hostname", COMPUTERNAME_MAX_LENGTH);

  const networkFields = {
    hostname: bulk ? `${hostname}01` : hostname,
    ip_mode: bulk ? "dhcp" : ipMode,
    static_ip: !bulk && ipMode === "static" ? staticIp : null,
    static_netmask: !bulk && ipMode === "static" ? staticNetmask : null,
    static_gateway: !bulk && ipMode === "static" ? staticGateway : null,
    static_dns: !bulk && ipMode === "static" && staticDns ? staticDns.split(",").map((s) => s.trim()) : null,
  };

  async function goToReview() {
    setError(null);
    try {
      const { xml } = await api.post<{ xml: string }>(
        `/organizations/${selectedOrgId}/templates/${templateId}/preview`,
        { ...networkFields, overrides: overrides ?? undefined },
      );
      setPreviewXml(xml);
      setStep(3);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to render preview.");
    }
  }

  async function deploy() {
    setSubmitting(true);
    setError(null);
    try {
      if (bulk) {
        await api.post<Deployment[]>(`/organizations/${selectedOrgId}/deployments/bulk`, {
          template_id: templateId,
          hypervisor_host_id: hypervisorHostId,
          hostname_prefix: hostname,
          count: bulkCount,
        });
        navigate("/deployments");
      } else {
        const deployment = await api.post<Deployment>(`/organizations/${selectedOrgId}/deployments`, {
          template_id: templateId,
          overrides: overrides ?? undefined,
          hypervisor_host_id: hypervisorHostId,
          ...networkFields,
        });
        navigate(`/deployments/${deployment.id}`);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to create deployment.");
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <h1 className="text-lg font-semibold">New deployment</h1>

      <div className="flex gap-2 text-xs">
        {STEPS.map((s, i) => (
          <div
            key={s}
            className={`rounded-full px-3 py-1 ${i === step ? "bg-blue-600 text-white" : "bg-neutral-100 text-neutral-500 dark:bg-neutral-800 dark:text-neutral-400"}`}
          >
            {i + 1}. {s}
          </div>
        ))}
      </div>

      {error && <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-400">{error}</div>}

      <div className="rounded-lg border border-neutral-200 bg-white dark:border-neutral-700 dark:bg-neutral-900 p-5">
        {step === 0 && (
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Deployment template</label>
            <Select
              className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={templateId}
              onChange={(e) => {
                setTemplateId(e.target.value);
                setOverrides(null); // tied to the previously selected template's own field set
              }}
            >
              <option value="">Select a template...</option>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
            {templateId && (
              <div className="mt-2 flex items-center gap-2">
                <button
                  type="button"
                  className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                  onClick={() => setShowCustomize(true)}
                >
                  Customize installation
                </button>
                {overrides && (
                  <>
                    <span className="text-xs text-blue-600 dark:text-blue-400">Customized for this deployment</span>
                    <button
                      type="button"
                      className="text-xs text-neutral-400 hover:text-neutral-700 dark:hover:text-neutral-200"
                      onClick={() => setOverrides(null)}
                    >
                      Reset
                    </button>
                  </>
                )}
              </div>
            )}
            <p className="mt-1 text-xs text-neutral-400">
              Change any of this template's settings for just this one deployment, without editing the
              template itself.
            </p>
          </div>
        )}

        {step === 1 && (
          <div>
            <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Hypervisor</label>
            <Select
              className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
              value={hypervisorHostId}
              onChange={(e) => setHypervisorHostId(e.target.value)}
            >
              <option value="">Select a hypervisor...</option>
              {hosts.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name} ({h.type})
                </option>
              ))}
            </Select>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-3">
            <label className="flex items-center gap-2 text-xs font-medium text-neutral-600 dark:text-neutral-400">
              <input type="checkbox" checked={bulk} onChange={(e) => setBulk(e.target.checked)} />
              Bulk deployment (creates multiple VMs from this template, DHCP only)
            </label>
            <div>
              <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">
                {bulk ? "Hostname prefix" : "Hostname"}
              </label>
              <input
                className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                placeholder={bulk ? "e.g. WEB- (becomes WEB-01, WEB-02, ...)" : undefined}
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
              />
              {hostnameError && <p className="mt-1 text-xs text-red-600">{hostnameError}</p>}
            </div>
            {bulk && (
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">Number of VMs</label>
                <input
                  type="number"
                  min={1}
                  max={50}
                  className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                  value={bulkCount}
                  onChange={(e) => setBulkCount(Number(e.target.value))}
                />
              </div>
            )}
            {!bulk && (
              <div>
                <label className="mb-1 block text-xs font-medium text-neutral-600 dark:text-neutral-400">IP configuration</label>
                <Select
                  className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                  value={ipMode}
                  onChange={(e) => setIpMode(e.target.value as IpMode)}
                >
                  <option value="dhcp">DHCP</option>
                  <option value="static">Static</option>
                </Select>
              </div>
            )}
            {!bulk && ipMode === "static" && (
              <div className="grid grid-cols-2 gap-3">
                <input
                  placeholder="IP address"
                  className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                  value={staticIp}
                  onChange={(e) => setStaticIp(e.target.value)}
                />
                <input
                  placeholder="Netmask (e.g. 255.255.255.0)"
                  className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                  value={staticNetmask}
                  onChange={(e) => setStaticNetmask(e.target.value)}
                />
                <input
                  placeholder="Gateway"
                  className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                  value={staticGateway}
                  onChange={(e) => setStaticGateway(e.target.value)}
                />
                <input
                  placeholder="DNS servers, comma-separated"
                  className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm dark:bg-neutral-900"
                  value={staticDns}
                  onChange={(e) => setStaticDns(e.target.value)}
                />
              </div>
            )}
          </div>
        )}

        {step === 3 && (
          <div>
            <p className="mb-2 text-xs text-neutral-500">
              Rendered autounattend.xml, this is exactly what will be built into the answer-file ISO.
            </p>
            <pre className="max-h-96 overflow-auto rounded-md bg-neutral-950 p-3 text-xs text-neutral-200">
              {previewXml}
            </pre>
          </div>
        )}

        {step === 4 && (
          <div className="text-sm text-neutral-600 dark:text-neutral-400">
            {bulk ? (
              <>
                Ready to deploy <span className="font-medium">{bulkCount}</span> VMs (
                <span className="font-medium">
                  {hostname}01
                  {"–"}
                  {hostname}
                  {String(bulkCount).padStart(2, "0")}
                </span>
                ) from template{" "}
              </>
            ) : (
              <>
                Ready to deploy <span className="font-medium">{hostname}</span> from template{" "}
              </>
            )}
            <span className="font-medium">{templates.find((t) => t.id === templateId)?.name}</span> onto{" "}
            <span className="font-medium">{hosts.find((h) => h.id === hypervisorHostId)?.name}</span>.
          </div>
        )}
      </div>

      <div className="flex justify-between">
        <button
          className="rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm disabled:opacity-40"
          disabled={step === 0}
          onClick={() => setStep(step - 1)}
        >
          Back
        </button>
        {step < 2 && (
          <button
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-40"
            disabled={step === 0 ? !templateId : !hypervisorHostId}
            onClick={() => setStep(step + 1)}
          >
            Next
          </button>
        )}
        {step === 2 && (
          <button
            className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white disabled:opacity-40"
            disabled={!hostname || !!hostnameError}
            onClick={goToReview}
          >
            Preview
          </button>
        )}
        {step === 3 && (
          <button className="rounded-md bg-blue-600 px-3 py-1.5 text-sm text-white" onClick={() => setStep(4)}>
            Continue
          </button>
        )}
        {step === 4 && (
          <button
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
            disabled={submitting}
            onClick={deploy}
          >
            {submitting ? "Deploying..." : "Deploy"}
          </button>
        )}
      </div>

      {showCustomize && selectedTemplate && (
        <TemplateFieldsForm
          orgId={selectedOrgId}
          hosts={hosts}
          diskLayouts={diskLayouts}
          isoAssets={isoAssets}
          appAssets={appAssets}
          existing={selectedTemplate}
          title="Customize installation"
          description="Changes apply to this deployment only - the template itself is untouched."
          showName={false}
          requirePassword={false}
          submitLabel="Apply"
          onClose={() => setShowCustomize(false)}
          onSubmit={async (body) => {
            setOverrides(cleanOverrides(body));
            setShowCustomize(false);
          }}
        />
      )}
    </div>
  );
}
