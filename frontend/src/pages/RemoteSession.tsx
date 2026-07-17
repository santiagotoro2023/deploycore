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
  const [rustdeskPassword, setRustdeskPassword] = useState<string | null>(null);
  const [showRustdeskPassword, setShowRustdeskPassword] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  const connect = useCallback(async () => {
    if (!selectedOrgId || !id) return;
    setError(null);
    setConnecting(true);
    setEmbedUrl(null);
    try {
      const session = await api.post<{ embed_url: string; rustdesk_password: string }>(
        `/organizations/${selectedOrgId}/managed-hosts/${id}/session`
      );
      setEmbedUrl(session.embed_url);
      setRustdeskPassword(session.rustdesk_password);
      setShowRustdeskPassword(true);
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

  // Redeeming the share_token is handled entirely by the embedded client's
  // own client-side JS (lejianwen/rustdesk-api-web's ljw.js, confirmed via
  // its source) - it registers the peer into that page's localStorage
  // (peers[id]), but does NOT navigate anywhere itself, landing on the
  // address book tab with one extra manual click needed. The SAME app's
  // own admin panel opens an already-known peer via a `#/<id>` hash route
  // instead (confirmed in its toWebClientLink()) - now that this session
  // is same-origin, we drive that exact navigation ourselves.
  //
  // Redeeming the token also writes a 'tmppwd' field onto that same peer
  // object, clearly INTENDED to make the connection auto-authenticate -
  // confirmed against the same source, though, nothing in the client's own
  // connection logic ever reads that specific key back (its only other use
  // is an unrelated live-connection option), so it silently does nothing
  // and the client falls back to prompting for a password regardless of
  // what was sent. The one thing that DOES skip the prompt, confirmed by
  // reading the ACTUAL working "remembered peer" path a few lines earlier
  // in the same file (getServerConf's peer-populating loop), is a peer
  // object with `password` (not `tmppwd`) set to
  // `stringToUint8Array(atob(x)).toString()` of the real password, plus
  // `remember: true`. Since this session is genuinely same-origin, we can
  // reach into the iframe's own localStorage directly and patch that onto
  // the SAME peer object ljw.js already created, using the identical
  // transform (our passwords are ASCII-safe base64-alphabet strings, so a
  // straight charCodeAt-per-character walk produces the same byte values
  // atob() would) - closing the actual gap instead of working around it.
  useEffect(() => {
    if (!embedUrl || !host?.rustdesk_id || !rustdeskPassword) return;
    const timer = setTimeout(() => {
      const win = iframeRef.current?.contentWindow;
      if (!win) return;
      try {
        const peers = JSON.parse(win.localStorage.getItem("peers") || "{}");
        const peer = peers[host.rustdesk_id];
        if (peer) {
          peer.password = Array.from(rustdeskPassword, (c) => c.charCodeAt(0)).join(",");
          peer.remember = true;
          win.localStorage.setItem("peers", JSON.stringify(peers));
        }
      } catch {
        // Best-effort - the visible password panel is the fallback either way.
      }
      win.location.hash = `/${host.rustdesk_id}`;
    }, 1500);
    return () => clearTimeout(timer);
  }, [embedUrl, host?.rustdesk_id, rustdeskPassword]);

  // "Connect" mode only: fetch this host's saved RDP credentials once the
  // session is up. No auto-type attempt (a prior postMessage-based one was
  // removed) - confirmed alongside the RustDesk password fix above that
  // webclient2 has no documented postMessage API to act on it at all, the
  // same reason Ctrl+Alt+Del isn't sent that way either (its own in-frame
  // toolbar already has a working one - use that instead). The copy
  // buttons below are the one mechanism that actually works.
  useEffect(() => {
    if (!isConnectMode || !selectedOrgId || !id || !embedUrl) return;
    let cancelled = false;
    api
      .get<ManagedHostRdpCredentials>(`/organizations/${selectedOrgId}/managed-hosts/${id}/rdp-credentials`)
      .then((creds) => {
        if (cancelled) return;
        setRdpCreds(creds);
        setShowCreds(true);
      })
      .catch(() => {
        if (!cancelled) setRdpCreds(null);
      });
    return () => {
      cancelled = true;
    };
  }, [isConnectMode, selectedOrgId, id, embedUrl]);

  const [copied, setCopied] = useState<string | null>(null);

  async function copyToClipboard(value: string, key: string) {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // navigator.clipboard needs a secure context AND page focus - both
      // usually true here, but silently rejects rather than throwing
      // something visible if either isn't (e.g. focus lost to the iframe a
      // moment before the click registers). document.execCommand('copy')
      // is deprecated but still broadly supported and doesn't share either
      // requirement, so it's a real fallback, not dead code.
      const el = document.createElement("textarea");
      el.value = value;
      el.style.position = "fixed";
      el.style.opacity = "0";
      document.body.appendChild(el);
      el.focus();
      el.select();
      try {
        document.execCommand("copy");
      } catch {
        // Nothing more to fall back to - the value is still visible on-screen to copy by hand.
      }
      document.body.removeChild(el);
    }
    setCopied(key);
    setTimeout(() => setCopied((c) => (c === key ? null : c)), 1500);
  }

  // Native browser fullscreen on the whole toolbar+viewer area (not just
  // the header row) - Fullscreen only renders the fullscreened element and
  // its descendants, so a ref on just the iframe's box (the original
  // approach) hid the RustDesk-password/RDP-credentials panels and their
  // copy buttons entirely the moment fullscreen engaged, right when the
  // embedded client's own connection state reset (a big resize) and
  // re-prompted for the password those panels exist to answer - confirmed
  // live as exactly what "asks again but doesn't even offer it" was.
  const fullscreenRef = useRef<HTMLDivElement>(null);
  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      fullscreenRef.current?.requestFullscreen?.();
    }
  }

  return (
    <div className="flex h-full flex-col">
      <div className="mb-4 flex items-center gap-3">
        <Link
          to="/remote-management"
          className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
        >
          <ArrowLeft size={12} strokeWidth={1.75} />
          Back
        </Link>
        <h1 className="text-lg font-semibold">{host ? host.name : "Connecting..."}</h1>
      </div>

      {/* Fullscreens as a whole (toolbar + credential panels + viewer), not
          just the viewer - Fullscreen only renders the fullscreened element
          and its descendants, so fullscreening just the iframe's box hid
          the RustDesk-password/RDP-credentials panels entirely the moment
          fullscreen engaged, right when the embedded client's own
          connection state resets (a big resize) and re-prompts for the
          password those panels exist to answer - confirmed live as exactly
          what "asks again but doesn't even offer it" was. */}
      <div ref={fullscreenRef} className="flex flex-1 flex-col bg-white dark:bg-neutral-950">
        {host?.enrolled && (
          <div className="mb-3 flex items-center justify-end gap-2">
            <span
              className="hidden items-center gap-1 text-xs text-neutral-400 sm:flex"
              title="Copy on your computer and paste into the remote session (and vice-versa) - clipboard is shared automatically while connected."
            >
              <ClipboardCheck size={13} strokeWidth={1.75} />
              Clipboard shared
            </span>
            {rustdeskPassword && (
              <button
                className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
                title="Show the RustDesk connection password"
                onClick={() => setShowRustdeskPassword(true)}
              >
                <KeySquare size={14} strokeWidth={1.75} />
                RustDesk password
              </button>
            )}
            {isConnectMode && rdpCreds && (rdpCreds.username || rdpCreds.password) && (
              <button
                className="flex items-center gap-1.5 rounded-md border border-neutral-300 dark:border-neutral-700 px-3 py-1.5 text-sm hover:bg-neutral-50 dark:hover:bg-neutral-800"
                title="Show the saved RDP credentials"
                onClick={() => setShowCreds(true)}
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

        {showRustdeskPassword && rustdeskPassword && (
          <div className="mb-3 flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm dark:border-blue-900 dark:bg-blue-950">
            <button
              className="flex shrink-0 items-center gap-1 rounded-md border border-blue-300 bg-white px-2 py-1 text-xs text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-neutral-900 dark:text-blue-400 dark:hover:bg-neutral-800"
              onClick={() => copyToClipboard(rustdeskPassword, "rustdesk-password")}
              title="Copy RustDesk password"
            >
              <Copy size={12} strokeWidth={1.75} />
              {copied === "rustdesk-password" ? "Copied" : "••••••••"}
            </button>
            <button
              className="ml-auto shrink-0 text-blue-400 hover:text-blue-600 dark:hover:text-blue-300"
              title="Dismiss"
              onClick={() => setShowRustdeskPassword(false)}
            >
              <X size={14} strokeWidth={1.75} />
            </button>
          </div>
        )}

        {isConnectMode && showCreds && rdpCreds && (rdpCreds.username || rdpCreds.password) && (
          <div className="mb-3 flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm dark:border-blue-900 dark:bg-blue-950">
            {rdpCreds.username && (
              <button
                className="flex shrink-0 items-center gap-1 rounded-md border border-blue-300 bg-white px-2 py-1 text-xs text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-neutral-900 dark:text-blue-400 dark:hover:bg-neutral-800"
                onClick={() => copyToClipboard(rdpCreds.username ?? "", "rdp-username")}
                title="Copy username"
              >
                <Copy size={12} strokeWidth={1.75} />
                {copied === "rdp-username" ? "Copied" : rdpCreds.username}
              </button>
            )}
            {rdpCreds.password && (
              <button
                className="flex shrink-0 items-center gap-1 rounded-md border border-blue-300 bg-white px-2 py-1 text-xs text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-neutral-900 dark:text-blue-400 dark:hover:bg-neutral-800"
                onClick={() => copyToClipboard(rdpCreds.password ?? "", "rdp-password")}
                title="Copy password"
              >
                <Copy size={12} strokeWidth={1.75} />
                {copied === "rdp-password" ? "Copied" : "••••••••"}
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

        <div className="flex flex-1 items-center justify-center overflow-hidden rounded-lg border border-neutral-200 bg-neutral-900 dark:border-neutral-800">
          {error && <p className="p-4 text-center text-sm text-red-400">{error}</p>}
          {!error && !host && (
            <div className="flex flex-col items-center gap-2 text-neutral-400">
              <Loader2 size={20} className="animate-spin" strokeWidth={1.75} />
              <p className="text-sm">Loading host...</p>
            </div>
          )}
          {!error && host && !host.enrolled && (
            <p className="max-w-sm p-4 text-center text-sm text-neutral-400">
              This host hasn't enrolled its Remote Management Agent yet. Go back and use "Install command" to set it
              up, then return here once it shows as enrolled.
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
    </div>
  );
}
