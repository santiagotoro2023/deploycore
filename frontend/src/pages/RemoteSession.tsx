import { ArrowLeft, ClipboardCheck, Copy, KeySquare, Loader2, Maximize, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { ManagedHost, ManagedHostRdpCredentials } from "../api/types";
import { useOrg } from "../state/org";

export default function RemoteSession() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  // "Connect" (vs. plain "Shadow") - see RemoteManagement.tsx's own two
  // buttons, both landing here, differing only by this query param.
  const isConnectMode = searchParams.get("mode") === "connect";
  const { selectedOrgId } = useOrg();
  const [host, setHost] = useState<ManagedHost | null>(null);
  const [embedUrl, setEmbedUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [rdpCreds, setRdpCreds] = useState<ManagedHostRdpCredentials | null>(null);
  const [showCreds, setShowCreds] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const viewerRef = useRef<HTMLDivElement>(null);

  const connect = useCallback(async () => {
    if (!selectedOrgId || !id) return;
    setError(null);
    setConnecting(true);
    setEmbedUrl(null);
    try {
      const session = await api.post<{ embed_url: string }>(
        `/organizations/${selectedOrgId}/managed-hosts/${id}/session`
      );
      setEmbedUrl(session.embed_url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the remote session.");
    } finally {
      setConnecting(false);
    }
  }, [selectedOrgId, id]);

  useEffect(() => {
    if (!selectedOrgId || !id) return;
    let cancelled = false;
    api
      .get<ManagedHost>(`/organizations/${selectedOrgId}/managed-hosts/${id}`)
      .then((h) => {
        if (cancelled) return;
        setHost(h);
        if (h.enrolled) connect();
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof ApiError ? err.message : "Failed to load this host.");
      });
    return () => {
      cancelled = true;
    };
  }, [selectedOrgId, id, connect]);

  // The embedded web client owns its own keyboard once focused, so a
  // Ctrl+Alt+Del button here can't be a synthetic key event (it wouldn't
  // cross the iframe boundary, and the browser swallows the real combo).
  // ponytail: the RustDesk web client already has its own in-frame
  // Ctrl+Alt+Del toolbar button, so this is the redundant convenience path -
  // it posts a message the embedded client is NOT confirmed to listen for
  // (research found no documented postMessage API for webclient2). Left in as
  // a no-op-if-unsupported nicety; wire it to the real mechanism if/when one
  // is confirmed, otherwise operators use the client's own toolbar button.
  function sendCtrlAltDel() {
    iframeRef.current?.contentWindow?.postMessage({ type: "ctrl_alt_del" }, "*");
  }

  // "Connect" mode only: fetch this host's saved RDP credentials once the
  // session is up, and attempt the same best-effort postMessage approach as
  // Ctrl+Alt+Del above - equally unconfirmed to actually be acted on by
  // webclient2, for the same reason (no documented API). The credentials
  // panel below is the fallback that always works regardless: shown either
  // way, with copy buttons, so the operator can type them in manually if the
  // auto-type attempt didn't take.
  useEffect(() => {
    if (!isConnectMode || !selectedOrgId || !id || !embedUrl) return;
    let cancelled = false;
    api
      .get<ManagedHostRdpCredentials>(`/organizations/${selectedOrgId}/managed-hosts/${id}/rdp-credentials`)
      .then((creds) => {
        if (cancelled) return;
        setRdpCreds(creds);
        setShowCreds(true);
        if (creds.username || creds.password) {
          // A short delay for the embedded client to actually finish
          // loading/connecting before it could plausibly act on this -
          // arbitrary, since there's no "ready" signal to wait on instead.
          setTimeout(() => {
            if (cancelled) return;
            iframeRef.current?.contentWindow?.postMessage(
              { type: "type_credentials", username: creds.username ?? "", password: creds.password ?? "" },
              "*"
            );
          }, 1500);
        }
      })
      .catch(() => {
        if (!cancelled) setRdpCreds(null);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isConnectMode, selectedOrgId, id, embedUrl]);

  function tryAutoType() {
    if (!rdpCreds) return;
    iframeRef.current?.contentWindow?.postMessage(
      { type: "type_credentials", username: rdpCreds.username ?? "", password: rdpCreds.password ?? "" },
      "*"
    );
  }

  async function copyToClipboard(value: string) {
    await navigator.clipboard.writeText(value);
  }

  // Native browser fullscreen on the viewer (iframe + its frame), so the
  // remote screen fills the whole display - the VNC/ESXi-console expectation.
  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      viewerRef.current?.requestFullscreen?.();
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link
            to="/remote-management"
            className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
          >
            <ArrowLeft size={12} strokeWidth={1.75} />
            Back
          </Link>
          <h1 className="text-lg font-semibold">{host ? host.name : "Connecting..."}</h1>
        </div>
        {host?.enrolled && (
          <div className="flex items-center gap-2">
            <span
              className="hidden items-center gap-1 text-xs text-neutral-400 sm:flex"
              title="Copy on your computer and paste into the remote session (and vice-versa) - clipboard is shared automatically while connected."
            >
              <ClipboardCheck size={13} strokeWidth={1.75} />
              Clipboard shared
            </span>
            <button
              className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
              title="Send Ctrl+Alt+Del"
              disabled={!embedUrl}
              onClick={sendCtrlAltDel}
            >
              <KeySquare size={14} strokeWidth={1.75} />
              Ctrl+Alt+Del
            </button>
            {isConnectMode && rdpCreds && (
              <button
                className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
                title="Retry auto-typing the saved RDP username/password, or show them again"
                onClick={() => {
                  setShowCreds(true);
                  tryAutoType();
                }}
              >
                <KeySquare size={14} strokeWidth={1.75} />
                RDP credentials
              </button>
            )}
            <button
              className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
              title="Fullscreen"
              disabled={!embedUrl}
              onClick={toggleFullscreen}
            >
              <Maximize size={14} strokeWidth={1.75} />
              Fullscreen
            </button>
            <button
              className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
              title="Reconnect"
              disabled={connecting}
              onClick={connect}
            >
              <RefreshCw size={14} strokeWidth={1.75} className={connecting ? "animate-spin" : ""} />
              Reconnect
            </button>
          </div>
        )}
      </div>

      {isConnectMode && showCreds && rdpCreds && (rdpCreds.username || rdpCreds.password) && (
        <div className="mb-3 flex items-center gap-4 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm dark:border-blue-900 dark:bg-blue-950">
          <span className="text-blue-700 dark:text-blue-400">
            Tried auto-typing this host's saved RDP credentials into the login screen - if that didn't take, type them
            in yourself:
          </span>
          {rdpCreds.username && (
            <button
              className="flex shrink-0 items-center gap-1 rounded-md border border-blue-300 bg-white px-2 py-1 text-xs text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-neutral-900 dark:text-blue-400 dark:hover:bg-neutral-800"
              onClick={() => copyToClipboard(rdpCreds.username ?? "")}
              title="Copy username"
            >
              <Copy size={12} strokeWidth={1.75} />
              {rdpCreds.username}
            </button>
          )}
          {rdpCreds.password && (
            <button
              className="flex shrink-0 items-center gap-1 rounded-md border border-blue-300 bg-white px-2 py-1 text-xs text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-neutral-900 dark:text-blue-400 dark:hover:bg-neutral-800"
              onClick={() => copyToClipboard(rdpCreds.password ?? "")}
              title="Copy password"
            >
              <Copy size={12} strokeWidth={1.75} />
              ••••••••
            </button>
          )}
          <button
            className="ml-auto shrink-0 text-blue-400 hover:text-blue-600 dark:hover:text-blue-300"
            title="Dismiss"
            onClick={() => setShowCreds(false)}
          >
            <X size={14} strokeWidth={1.75} />
          </button>
        </div>
      )}

      <div
        ref={viewerRef}
        className="flex flex-1 items-center justify-center overflow-hidden rounded-lg border border-neutral-200 bg-neutral-900 dark:border-neutral-800"
      >
        {error && <p className="p-4 text-center text-sm text-red-400">{error}</p>}
        {!error && !host && (
          <div className="flex flex-col items-center gap-2 text-neutral-400">
            <Loader2 size={20} className="animate-spin" strokeWidth={1.75} />
            <p className="text-sm">Loading host...</p>
          </div>
        )}
        {!error && host && !host.enrolled && (
          <p className="max-w-sm p-4 text-center text-sm text-neutral-400">
            This host hasn't enrolled its Remote Management Agent yet. Go back and use "Install command" to set it up,
            then return here once it shows as enrolled.
          </p>
        )}
        {!error && host?.enrolled && !embedUrl && (
          <div className="flex flex-col items-center gap-2 text-neutral-400">
            <Loader2 size={20} className="animate-spin" strokeWidth={1.75} />
            <p className="text-sm">Establishing a secure session...</p>
          </div>
        )}
        {!error && embedUrl && (
          <iframe
            ref={iframeRef}
            src={embedUrl}
            title={host?.name ?? "Remote session"}
            className="h-full w-full border-0"
            allow="fullscreen; clipboard-read; clipboard-write"
          />
        )}
      </div>
    </div>
  );
}
