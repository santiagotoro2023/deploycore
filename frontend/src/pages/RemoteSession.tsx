import { ArrowLeft, ClipboardCheck, Copy, Keyboard, KeySquare, Loader2, Maximize, RefreshCw, X } from "lucide-react";
// Namespace import, not a default import: the ambient declaration below
// uses `export =` (matching the real package's CommonJS shape), and this
// tsconfig has no esModuleInterop set - `import * as X` is the form that
// works correctly against `export =` regardless of that flag.
import * as Guacamole from "guacamole-common-js";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, getToken } from "../api/client";
import { ManagedHost, ManagedHostRdpCredentials } from "../api/types";
import { useOrg } from "../state/org";

// Guacamole.Client's own numeric connection states (its source has no
// exported enum for these, just documents the numbers) - 3 is
// STATE_CONNECTED, the only one this page acts on.
const GUAC_STATE_CONNECTED = 3;

interface IceServersResponse {
  turn_host: string;
  turn_port: number;
  turn_username: string;
  turn_password: string;
}

export default function RemoteSession() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  // "Connect" (vs. plain "Shadow") - see RemoteManagement.tsx's own two
  // buttons, both landing here, differing only by this query param.
  const isConnectMode = searchParams.get("mode") === "connect";
  const { selectedOrgId } = useOrg();
  const [host, setHost] = useState<ManagedHost | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [sessionReady, setSessionReady] = useState(false);
  const [rdpCreds, setRdpCreds] = useState<ManagedHostRdpCredentials | null>(null);
  const [showCredsPanel, setShowCredsPanel] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  // Shadow mode
  const videoRef = useRef<HTMLVideoElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const dataChannelRef = useRef<RTCDataChannel | null>(null);
  // Connect mode
  const guacClientRef = useRef<Guacamole.Client | null>(null);
  const guacMouseRef = useRef<Guacamole.Mouse | null>(null);
  const guacKeyboardRef = useRef<Guacamole.Keyboard | null>(null);
  // Shared - the bordered viewer box both modes render into
  const viewerRef = useRef<HTMLDivElement>(null);
  const fullscreenRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onChange = () => setIsFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const teardown = useCallback(() => {
    wsRef.current?.close();
    wsRef.current = null;
    pcRef.current?.close();
    pcRef.current = null;
    dataChannelRef.current = null;
    guacClientRef.current?.disconnect();
    guacClientRef.current = null;
    guacMouseRef.current = null;
    guacKeyboardRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    if (viewerRef.current) viewerRef.current.replaceChildren();
    setSessionReady(false);
  }, []);

  // Physical, layout-independent key identity (event.code, e.g. "KeyA",
  // "Enter") - not event.key. The agent maps this to a Windows virtual-key
  // code itself (see remote-agent/PROTOCOL.md) - nothing here is
  // locale-specific.
  const sendDataChannel = useCallback((message: Record<string, unknown>) => {
    const channel = dataChannelRef.current;
    if (channel && channel.readyState === "open") channel.send(JSON.stringify(message));
  }, []);

  // A browser can never actually intercept the real Ctrl+Alt+Del combo (the
  // OS reserves it), so both modes need an explicit send instead of relying
  // on a keyboard shortcut. Shadow: the agent's own "cad" data-channel
  // message (SendSAS - see PROTOCOL.md). Connect: Guacamole/FreeRDP's
  // documented convention for a synthetic Ctrl+Alt+Del - press Control_L,
  // Alt_L, Delete as X11 keysyms, then release in reverse order.
  const sendCtrlAltDel = useCallback(() => {
    if (isConnectMode) {
      const client = guacClientRef.current;
      if (!client) return;
      const CTRL_L = 0xffe3;
      const ALT_L = 0xffe9;
      const DELETE = 0xffff;
      client.sendKeyEvent(1, CTRL_L);
      client.sendKeyEvent(1, ALT_L);
      client.sendKeyEvent(1, DELETE);
      client.sendKeyEvent(0, DELETE);
      client.sendKeyEvent(0, ALT_L);
      client.sendKeyEvent(0, CTRL_L);
    } else {
      sendDataChannel({ t: "cad" });
    }
  }, [isConnectMode, sendDataChannel]);

  const wireShadowInput = useCallback(() => {
    const container = viewerRef.current;
    const video = videoRef.current;
    if (!container || !video) return () => {};

    const toRemoteCoords = (clientX: number, clientY: number) => {
      const rect = video.getBoundingClientRect();
      const vw = video.videoWidth || rect.width;
      const vh = video.videoHeight || rect.height;
      return {
        x: Math.round(((clientX - rect.left) / rect.width) * vw),
        y: Math.round(((clientY - rect.top) / rect.height) * vh),
      };
    };

    const onMouseMove = (e: MouseEvent) => sendDataChannel({ t: "mousemove", ...toRemoteCoords(e.clientX, e.clientY) });
    const onMouseDown = (e: MouseEvent) => sendDataChannel({ t: "mousedown", button: e.button });
    const onMouseUp = (e: MouseEvent) => sendDataChannel({ t: "mouseup", button: e.button });
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      sendDataChannel({ t: "wheel", dy: e.deltaY });
    };
    const onKeyDown = (e: KeyboardEvent) => {
      e.preventDefault();
      sendDataChannel({ t: "keydown", code: e.code });
    };
    const onKeyUp = (e: KeyboardEvent) => {
      e.preventDefault();
      sendDataChannel({ t: "keyup", code: e.code });
    };
    const onPaste = (e: ClipboardEvent) => {
      const text = e.clipboardData?.getData("text");
      if (text) sendDataChannel({ t: "clipboard", text });
    };

    container.addEventListener("mousemove", onMouseMove);
    container.addEventListener("mousedown", onMouseDown);
    container.addEventListener("mouseup", onMouseUp);
    container.addEventListener("wheel", onWheel, { passive: false });
    container.addEventListener("paste", onPaste);
    container.tabIndex = 0;
    container.addEventListener("keydown", onKeyDown);
    container.addEventListener("keyup", onKeyUp);

    return () => {
      container.removeEventListener("mousemove", onMouseMove);
      container.removeEventListener("mousedown", onMouseDown);
      container.removeEventListener("mouseup", onMouseUp);
      container.removeEventListener("wheel", onWheel);
      container.removeEventListener("paste", onPaste);
      container.removeEventListener("keydown", onKeyDown);
      container.removeEventListener("keyup", onKeyUp);
    };
  }, [sendDataChannel]);

  const connectShadow = useCallback(
    async (hostId: string) => {
      const iceServers = await api.get<IceServersResponse>(`/organizations/${selectedOrgId}/managed-hosts/ice-servers`);
      const pc = new RTCPeerConnection({
        iceServers: [
          { urls: `stun:${iceServers.turn_host}:${iceServers.turn_port}` },
          {
            urls: `turn:${iceServers.turn_host}:${iceServers.turn_port}`,
            username: iceServers.turn_username,
            credential: iceServers.turn_password,
          },
        ],
      });
      pcRef.current = pc;

      const wsProto = location.protocol === "https:" ? "wss" : "ws";
      const token = getToken() ?? "";
      const ws = new WebSocket(
        `${wsProto}://${location.host}/api/organizations/${selectedOrgId}/managed-hosts/${hostId}/session` +
          `?mode=shadow&token=${encodeURIComponent(token)}`
      );
      wsRef.current = ws;

      pc.onicecandidate = (event) => {
        if (event.candidate) {
          ws.send(
            JSON.stringify({
              kind: "ice",
              candidate: event.candidate.candidate,
              sdpMid: event.candidate.sdpMid,
              sdpMLineIndex: event.candidate.sdpMLineIndex,
            })
          );
        }
      };
      pc.ontrack = (event) => {
        if (!videoRef.current) return;
        // event.streams can be empty if the offering side's SDP doesn't
        // include a stream/track (msid) association - a real, common
        // cross-implementation WebRTC gotcha, not specific to any one
        // stack. Constructing a MediaStream directly from the track is the
        // standard, safe fallback: it doesn't depend on the offering side
        // having set msid up correctly at all.
        videoRef.current.srcObject = event.streams[0] ?? new MediaStream([event.track]);
      };
      pc.ondatachannel = (event) => {
        dataChannelRef.current = event.channel;
        event.channel.onopen = () => setSessionReady(true);
        event.channel.onmessage = (msg) => {
          try {
            const data = JSON.parse(msg.data);
            if (data.t === "clipboard" && typeof data.text === "string") {
              navigator.clipboard.writeText(data.text).catch(() => {});
            }
          } catch {
            // Not a message this page understands - ignore rather than throw.
          }
        };
      };

      ws.onmessage = async (event) => {
        let message: { type?: string; kind?: string; sdp?: string; candidate?: string; sdpMid?: string; sdpMLineIndex?: number };
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        if (message.type !== "signal") return;
        if (message.kind === "offer" && message.sdp) {
          await pc.setRemoteDescription({ type: "offer", sdp: message.sdp });
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          ws.send(JSON.stringify({ kind: "answer", sdp: answer.sdp }));
        } else if (message.kind === "ice" && message.candidate) {
          try {
            await pc.addIceCandidate({
              candidate: message.candidate,
              sdpMid: message.sdpMid,
              sdpMLineIndex: message.sdpMLineIndex,
            });
          } catch {
            // A candidate arriving before setRemoteDescription is a known,
            // harmless race - ICE gathers plenty more either way.
          }
        }
      };
      ws.onerror = () => setError("Could not reach the remote session.");
      ws.onclose = (event) => {
        setSessionReady(false);
        // A non-normal close with a server-supplied reason (agent not
        // connected, etc. - see managed_hosts.py's _authenticate_ws) is more
        // useful on screen than nothing, matching the same improvement made
        // for Connect mode's own close reasons.
        if (event.code !== 1000 && event.reason) setError(event.reason);
      };
    },
    [selectedOrgId]
  );

  const connectRdp = useCallback(
    (hostId: string) => {
      const wsProto = location.protocol === "https:" ? "wss" : "ws";
      const token = getToken() ?? "";
      const width = viewerRef.current?.clientWidth || 1280;
      const height = viewerRef.current?.clientHeight || 800;
      const tunnel = new Guacamole.WebSocketTunnel(
        `${wsProto}://${location.host}/api/organizations/${selectedOrgId}/managed-hosts/${hostId}/session` +
          `?mode=connect&token=${encodeURIComponent(token)}&w=${width}&h=${height}`
      );
      const client = new Guacamole.Client(tunnel);
      guacClientRef.current = client;

      client.onerror = (status) => setError(status.message || "The remote desktop session failed.");
      client.onstatechange = (state) => {
        if (state === GUAC_STATE_CONNECTED) setSessionReady(true);
      };
      client.onclipboard = (stream, mimetype) => {
        if (!mimetype.startsWith("text/")) return;
        const reader = new Guacamole.StringReader(stream);
        let text = "";
        reader.ontext = (chunk) => {
          text += chunk;
        };
        reader.onend = () => {
          navigator.clipboard.writeText(text).catch(() => {});
        };
      };

      const display = client.getDisplay();
      if (viewerRef.current) {
        viewerRef.current.replaceChildren(display.getElement());
      }
      client.connect();

      const displayElement = display.getElement();
      const mouse = new Guacamole.Mouse(displayElement);
      mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = (state) => client.sendMouseState(state);
      guacMouseRef.current = mouse;

      const keyboard = new Guacamole.Keyboard(document);
      keyboard.onkeydown = (keysym) => client.sendKeyEvent(1, keysym);
      keyboard.onkeyup = (keysym) => client.sendKeyEvent(0, keysym);
      guacKeyboardRef.current = keyboard;

      const onPaste = (e: ClipboardEvent) => {
        const text = e.clipboardData?.getData("text");
        if (!text) return;
        const stream = client.createClipboardStream("text/plain");
        const writer = new Guacamole.StringWriter(stream);
        writer.sendText(text);
        writer.sendEnd();
      };
      displayElement.addEventListener("paste", onPaste);
    },
    [selectedOrgId]
  );

  const connect = useCallback(async () => {
    if (!selectedOrgId || !id) return;
    setError(null);
    setConnecting(true);
    teardown();
    try {
      if (isConnectMode) {
        connectRdp(id);
      } else {
        await connectShadow(id);
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not start the remote session.");
    } finally {
      setConnecting(false);
    }
  }, [selectedOrgId, id, isConnectMode, connectRdp, connectShadow, teardown]);

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
      teardown();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedOrgId, id]);

  // Attaches Shadow's own input handling once the data channel is actually
  // open - Connect mode wires its own mouse/keyboard directly in
  // connectRdp() above (Guacamole.Mouse/Keyboard need the display element,
  // which doesn't exist until client.connect() has run).
  useEffect(() => {
    if (isConnectMode || !sessionReady) return;
    return wireShadowInput();
  }, [isConnectMode, sessionReady, wireShadowInput]);

  // Keeps the remote screen matched to the viewer box's actual size -
  // Shadow sends its own resize over the data channel (the agent applies it
  // exactly, no snapping - see PROTOCOL.md); Connect uses Guacamole's own
  // sendSize, which rides RDP's native Display Control channel for a real,
  // live resize. Debounced since a resize drag fires this dozens of times a
  // second.
  useEffect(() => {
    if (!sessionReady || !viewerRef.current) return;
    let debounce: ReturnType<typeof setTimeout>;
    const apply = () => {
      const el = viewerRef.current;
      if (!el) return;
      if (isConnectMode) guacClientRef.current?.sendSize(el.clientWidth, el.clientHeight);
      else sendDataChannel({ t: "resize", w: el.clientWidth, h: el.clientHeight });
    };
    const observer = new ResizeObserver(() => {
      clearTimeout(debounce);
      debounce = setTimeout(apply, 400);
    });
    observer.observe(viewerRef.current);
    return () => {
      clearTimeout(debounce);
      observer.disconnect();
    };
  }, [sessionReady, isConnectMode, sendDataChannel]);

  // "Connect" mode only: fetch this host's saved RDP credentials once the
  // session is up, for the credentials panel's copy buttons - the session
  // itself auto-authenticates now (guacd gets them directly from the
  // backend), this is just so an operator can see/copy the account in use.
  useEffect(() => {
    if (!isConnectMode || !selectedOrgId || !id || !sessionReady) return;
    let cancelled = false;
    api
      .get<ManagedHostRdpCredentials>(`/organizations/${selectedOrgId}/managed-hosts/${id}/rdp-credentials`)
      .then((creds) => {
        if (!cancelled) setRdpCreds(creds);
      })
      .catch(() => {
        if (!cancelled) setRdpCreds(null);
      });
    return () => {
      cancelled = true;
    };
  }, [isConnectMode, selectedOrgId, id, sessionReady]);

  async function copyToClipboard(value: string, key: string) {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // navigator.clipboard needs a secure context AND page focus - both
      // usually true here, but silently rejects rather than throwing
      // something visible if either isn't. document.execCommand('copy') is
      // deprecated but still broadly supported and doesn't share either
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

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen();
    } else {
      fullscreenRef.current?.requestFullscreen?.();
    }
  }

  const hasCreds = isConnectMode && !!rdpCreds && (!!rdpCreds.username || !!rdpCreds.password);

  return (
    <div className="flex h-full flex-col">
      {/* Fullscreens the toolbar + credential bar + viewer together, not
          just the viewer - Fullscreen only renders the fullscreened element
          and its descendants. The toolbar and credential bar are hidden
          WHILE fullscreen instead (isFullscreen), for a clean view - both
          stay reachable via Escape. */}
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
                  title="Send Ctrl+Alt+Del"
                  disabled={!sessionReady}
                  onClick={sendCtrlAltDel}
                >
                  <Keyboard size={12} strokeWidth={1.75} />
                  Ctrl+Alt+Del
                </button>
                <button
                  className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-1.5 py-0.5 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
                  title="Fullscreen"
                  disabled={!sessionReady}
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

        <div className="relative flex flex-1 items-center justify-center overflow-hidden rounded-lg border border-neutral-200 bg-neutral-900 dark:border-neutral-800">
          {/* Unconditionally mounted from the component's very first render
              (never gated on host/sessionReady) - connectShadow/connectRdp
              need a real, already-attached DOM node the moment they start
              wiring up async callbacks (ontrack, onstatechange, etc.), not
              one that only appears a render later once `host` has loaded.
              Every other state below layers on top as an absolute overlay
              instead of replacing it. */}
          <div ref={viewerRef} className="h-full w-full">
            {!isConnectMode && <video ref={videoRef} autoPlay muted className="h-full w-full object-contain" />}
          </div>

          {error && (
            <p className="absolute inset-0 flex items-center justify-center p-4 text-center text-sm text-red-400">
              {error}
            </p>
          )}
          {!error && !host && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-neutral-400">
              <Loader2 size={20} className="animate-spin" strokeWidth={1.75} />
              <p className="text-sm">Loading host...</p>
            </div>
          )}
          {!error && host && !host.enrolled && (
            <p className="absolute inset-0 flex max-w-sm items-center justify-center p-4 text-center text-sm text-neutral-400">
              This host hasn't enrolled its Remote Management Agent yet. Go back and use "Install command" to set it
              up, then return here once it shows as enrolled.
            </p>
          )}
          {!error && host?.enrolled && !sessionReady && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-neutral-900 text-neutral-400">
              <Loader2 size={20} className="animate-spin" strokeWidth={1.75} />
              <p className="text-sm">Establishing a secure session...</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
