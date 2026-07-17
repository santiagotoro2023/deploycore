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
  const [rustdeskPassword, setRustdeskPassword] = useState<string | null>(null);
  const [showCredsPanel, setShowCredsPanel] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const onChange = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

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

  // Two DIFFERENT things, both needed, confirmed against RustDesk's own
  // Flutter source (flutter/lib/web/bridge.dart) rather than assumed:
  //
  // 1. sessionSetViewStyle sets 'adaptive' (flutter/lib/consts.dart's
  //    kRemoteViewStyleAdaptive) instead of 'original' - VISUAL scaling of
  //    whatever the remote resolution already is, down/up to fit the
  //    container. On its own this still leaves the VM's actual desktop
  //    resolution unchanged (whatever it was set to on the ESXi console) -
  //    scaled to fit, not resized, still the wrong aspect ratio/DPI for
  //    the browser window.
  // 2. sessionChangeResolution actually changes the VM's own display
  //    resolution to match - a real, separate feature (bridge.dart's
  //    changeResolution), confirmed genuinely present in the web build,
  //    not just desktop clients.
  //
  // Both go through the exact same mechanism: a plain global JS function,
  // `window.setByName(name, jsonEncode(value))` - not a postMessage API -
  // so same-origin access lets us call them directly, the same way the
  // embedded app's own UI would. display: 0 assumes a single monitor,
  // true for every VM this connects to.
  //
  // Confirmed live this needs real error handling, not just "doesn't
  // throw": setByName('option:session', ...) throws synchronously
  // ("Cannot read properties of undefined (reading 'setOption')") for
  // however long the embedded client's own session object isn't
  // initialized yet - and an uncaught throw from that first call was
  // aborting the rest of this function, meaning change_resolution (the
  // one that actually matters for the reported black-bar/no-resize issue)
  // never even ran as long as the first one kept failing. Each call is now
  // independent - one failing can't block the other.
  const fitDisplayToWindow = useCallback(() => {
    const win = iframeRef.current?.contentWindow as (Window & { setByName?: (n: string, v: string) => void }) | undefined;
    const width = iframeRef.current?.clientWidth;
    const height = iframeRef.current?.clientHeight;
    if (!win?.setByName) return;
    try {
      win.setByName("option:session", JSON.stringify({ name: "view_style", value: "adaptive" }));
    } catch {
      // Session not ready yet - the retry loop below covers this.
    }
    if (width && height) {
      try {
        win.setByName("change_resolution", JSON.stringify({ display: 0, width, height }));
      } catch {
        // Same as above.
      }
    }
  }, []);

  // Retries on an interval rather than a couple of fixed delays - confirmed
  // live the embedded client's session object can still not exist 5+
  // seconds after embedUrl is set (a real connection, especially over a
  // relay, can genuinely take longer than that to finish establishing) -
  // every 2s for a full minute comfortably covers that without needing to
  // guess a single "long enough" number. Harmless to keep calling once it
  // succeeds - setByName no-ops rather than erroring on an already-correct
  // value.
  useEffect(() => {
    if (!embedUrl || !host?.rustdesk_id) return;
    const interval = setInterval(fitDisplayToWindow, 2000);
    const stop = setTimeout(() => clearInterval(interval), 60000);
    return () => {
      clearInterval(interval);
      clearTimeout(stop);
    };
  }, [embedUrl, host?.rustdesk_id, fitDisplayToWindow]);

  // Entering/leaving fullscreen is itself a big resize (a new window size
  // to match, and apparently enough of a state change that the embedded
  // client falls back to unscaled 'original' again too, the same reset
  // that re-prompts for the password) - reapplying here covers both at
  // once. Slightly longer than the connect-time delay above: needs the
  // CSS fullscreen transition to have actually finished so the iframe's
  // own clientWidth/clientHeight reflect the new size, not the old one.
  useEffect(() => {
    if (!embedUrl) return;
    const timer = setTimeout(fitDisplayToWindow, 700);
    return () => clearTimeout(timer);
  }, [isFullscreen, embedUrl, fitDisplayToWindow]);

  // Not just fullscreen - ANY resize of the viewer box (the browser window
  // itself resizing, the sidebar collapsing, DevTools opening, whatever)
  // should keep the remote resolution matched to it, the same way this
  // page's own layout responds to its container rather than staying a
  // fixed size. Debounced since a live resize drag fires this dozens of
  // times a second and change_resolution is a real, non-free operation on
  // the agent side, not free-of-cost CSS.
  useEffect(() => {
    if (!embedUrl || !iframeRef.current) return;
    let debounce: ReturnType<typeof setTimeout>;
    const observer = new ResizeObserver(() => {
      clearTimeout(debounce);
      debounce = setTimeout(fitDisplayToWindow, 500);
    });
    observer.observe(iframeRef.current);
    return () => {
      clearTimeout(debounce);
      observer.disconnect();
    };
  }, [embedUrl, fitDisplayToWindow]);

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

  // Fullscreens the toolbar+viewer area, not just the iframe's own box -
  // otherwise Fullscreen (which only renders the fullscreened element and
  // its descendants) hid the credential panels' copy buttons entirely the
  // moment it engaged, right when the embedded client's own connection
  // state resets (a big resize) and re-prompts for the password those
  // panels exist to answer. DeployCore's own toolbar/panels are then
  // hidden WHILE fullscreen instead (isFullscreen, declared up top since
  // the view-style effect above also needs it) - once requested, so a real
  // password re-prompt in fullscreen just means pressing Escape once to
  // reach the copy button, rather than never having anywhere to render at all.
  const fullscreenRef = useRef<HTMLDivElement>(null);
  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      fullscreenRef.current?.requestFullscreen?.();
    }
  }

  const hasCreds =
    !!rustdeskPassword || (isConnectMode && !!rdpCreds && (!!rdpCreds.username || !!rdpCreds.password));

  return (
    <div className="flex h-full flex-col">
      {/* Fullscreens the toolbar + credential bar + viewer together, not
          just the viewer - Fullscreen only renders the fullscreened element
          and its descendants, so fullscreening just the iframe's box hid
          the credential bar's copy buttons entirely the moment fullscreen
          engaged, right when the embedded client's own connection state
          resets (a big resize) and re-prompts for the password that bar
          exists to answer. The toolbar and credential bar are hidden WHILE
          fullscreen instead (isFullscreen), for a clean view - both stay
          reachable via Escape if that reset happens again. One combined
          row (not a separate title row above it) - direct request to
          maximize actual screen space over chrome. */}
      <div ref={fullscreenRef} className="flex flex-1 flex-col bg-white dark:bg-neutral-950">
        {!isFullscreen && (
          <div className="mb-1.5 flex items-center gap-2">
            <Link
              to="/remote-management"
              className="flex shrink-0 items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-1.5 py-0.5 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
            >
              <ArrowLeft size={11} strokeWidth={1.75} />
              Back
            </Link>
            <h1 className="truncate text-xs font-semibold">{host ? host.name : "Connecting..."}</h1>
            {host?.enrolled && (
              <div className="ml-auto flex shrink-0 items-center gap-1.5">
                <span
                  className="hidden items-center gap-1 text-xs text-neutral-400 sm:flex"
                  title="Copy on your computer and paste into the remote session (and vice-versa) - clipboard is shared automatically while connected."
                >
                  <ClipboardCheck size={12} strokeWidth={1.75} />
                  Clipboard shared
                </span>
                {hasCreds && (
                  <button
                    className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-1.5 py-0.5 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800"
                    title="Show/hide connection credentials"
                    onClick={() => setShowCredsPanel((v) => !v)}
                  >
                    <KeySquare size={12} strokeWidth={1.75} />
                    Credentials
                  </button>
                )}
                <button
                  className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-1.5 py-0.5 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
                  title="Fullscreen"
                  disabled={!embedUrl}
                  onClick={toggleFullscreen}
                >
                  <Maximize size={12} strokeWidth={1.75} />
                  Fullscreen
                </button>
                <button
                  className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-1.5 py-0.5 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
                  title="Reconnect"
                  disabled={connecting}
                  onClick={connect}
                >
                  <RefreshCw size={12} strokeWidth={1.75} className={connecting ? "animate-spin" : ""} />
                  Reconnect
                </button>
              </div>
            )}
          </div>
        )}

        {!isFullscreen && showCredsPanel && hasCreds && (
          <div className="mb-2 flex items-center gap-1.5 rounded-md border border-blue-200 bg-blue-50 px-2 py-1 text-xs dark:border-blue-900 dark:bg-blue-950">
            {rustdeskPassword && (
              <button
                className="flex shrink-0 items-center gap-1 rounded-md border border-blue-300 bg-white px-1.5 py-0.5 text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-neutral-900 dark:text-blue-400 dark:hover:bg-neutral-800"
                onClick={() => copyToClipboard(rustdeskPassword, "rustdesk-password")}
                title="Copy RustDesk password"
              >
                <Copy size={11} strokeWidth={1.75} />
                {copied === "rustdesk-password" ? "Copied" : "••••••••"}
              </button>
            )}
            {isConnectMode && rdpCreds?.username && (
              <button
                className="flex shrink-0 items-center gap-1 rounded-md border border-blue-300 bg-white px-1.5 py-0.5 text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-neutral-900 dark:text-blue-400 dark:hover:bg-neutral-800"
                onClick={() => copyToClipboard(rdpCreds.username ?? "", "rdp-username")}
                title="Copy RDP username"
              >
                <Copy size={11} strokeWidth={1.75} />
                {copied === "rdp-username" ? "Copied" : rdpCreds.username}
              </button>
            )}
            {isConnectMode && rdpCreds?.password && (
              <button
                className="flex shrink-0 items-center gap-1 rounded-md border border-blue-300 bg-white px-1.5 py-0.5 text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-neutral-900 dark:text-blue-400 dark:hover:bg-neutral-800"
                onClick={() => copyToClipboard(rdpCreds.password ?? "", "rdp-password")}
                title="Copy RDP password"
              >
                <Copy size={11} strokeWidth={1.75} />
                {copied === "rdp-password" ? "Copied" : "••••••••"}
              </button>
            )}
            <button
              className="ml-auto shrink-0 text-blue-400 hover:text-blue-600 dark:hover:text-blue-300"
              title="Dismiss"
              onClick={() => setShowCredsPanel(false)}
            >
              <X size={13} strokeWidth={1.75} />
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
