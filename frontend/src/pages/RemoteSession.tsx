import { ArrowLeft, ChevronDown, Copy, Keyboard, KeySquare, Loader2, Maximize, Power, PowerOff, RefreshCw, RotateCcw, X } from "lucide-react";
// Namespace import, not a default import: the ambient declaration below
// uses `export =` (matching the real package's CommonJS shape), and this
// tsconfig has no esModuleInterop set - `import * as X` is the form that
// works correctly against `export =` regardless of that flag.
import * as Guacamole from "guacamole-common-js";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, getToken } from "../api/client";
import ConfirmDialog from "../components/ConfirmDialog";
import { ManagedHost, ManagedHostRdpCredentials } from "../api/types";
import { useOrg } from "../state/org";

// Guacamole.Client's own numeric connection states (its source has no
// exported enum for these, just documents the numbers) - 3 is
// STATE_CONNECTED, the only one this page acts on.
const GUAC_STATE_CONNECTED = 3;

// guacamole-common-js is an older CommonJS/UMD package - never actually
// verified at runtime through this project's own bundler (no working `npm
// install` in the environment this was written in, only a hand-written
// ambient .d.ts that satisfies the TYPE checker, which says nothing about
// what the ACTUAL JS module shape is once esbuild/Vite applies its own
// CJS interop for `import * as X`). Some bundlers spread a CJS module's
// own exports directly onto the namespace object (what every call site
// below assumes); others nest them under a synthetic `.default` instead -
// a well-known interop inconsistency for this exact import style, not
// specific to this package. Resolved defensively here, once, rather than
// assumed at every call site - if this guess is also wrong, the try/catch
// in connectRdp below at least reports THAT clearly instead of the same
// unhelpful generic error a second time.
const GuacamoleLib: typeof Guacamole =
  (Guacamole as unknown as { WebSocketTunnel?: unknown }).WebSocketTunnel
    ? Guacamole
    : ((Guacamole as unknown as { default?: typeof Guacamole }).default ?? Guacamole);

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
    // Drop the key handlers and release any keys Guacamole thinks are still
    // held before letting go of the object - otherwise navigating away
    // mid-keypress leaves a stuck modifier and (with the old document-level
    // binding) swallowed keystrokes for the rest of the app.
    if (guacKeyboardRef.current) {
      guacKeyboardRef.current.onkeydown = null;
      guacKeyboardRef.current.onkeyup = null;
      try {
        guacKeyboardRef.current.reset();
      } catch {
        // reset() is best-effort cleanup; never block teardown on it.
      }
      guacKeyboardRef.current = null;
    }
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
      // Its own try/catch, not left to the generic one in connect() below -
      // if guacamole-common-js's runtime shape guess (GuacamoleLib, above)
      // is ALSO wrong, this reports that specifically instead of falling
      // through to "Could not start the remote session." a second time.
      try {
        const wsProto = location.protocol === "https:" ? "wss" : "ws";
        const token = getToken() ?? "";
        const width = viewerRef.current?.clientWidth || 1280;
        const height = viewerRef.current?.clientHeight || 800;
        // The tunnel URL must be the bare session endpoint with NO query
        // string. Guacamole.Client.connect(data) hands `data` to
        // WebSocketTunnel.connect, which builds the socket URL as
        // `tunnelURL + "?" + data` itself. Passing the params in the
        // constructor URL and then calling connect() with no argument made the
        // tunnel append the literal "?undefined", corrupting the last query
        // param (e.g. h="800?undefined") and crashing the backend route before
        // the guacd handshake. So: bare URL here, params via connect() below.
        const tunnel = new GuacamoleLib.WebSocketTunnel(
          `${wsProto}://${location.host}/api/organizations/${selectedOrgId}/managed-hosts/${hostId}/session`
        );
        const client = new GuacamoleLib.Client(tunnel);
        guacClientRef.current = client;

        // Client.onerror only fires for guacd's own in-protocol "error"
        // instructions; tunnel-level failures (subprotocol refusal, a parser
        // error, the receive timeout) ONLY reach tunnel.onerror. Wiring both
        // is what turns the old silent "Establishing a secure session..."
        // forever-spinner into a real, readable error.
        tunnel.onerror = (status) => setError(status.message || "The remote session's connection failed.");
        client.onerror = (status) => setError(status.message || "The remote desktop session failed.");
        // guacd asks for credentials it considers missing via "required" and
        // then WAITS for an argv reply. Unanswered, that's indistinguishable
        // from a dead session until the tunnel's own 15s timeout fires as a
        // bare "Server timeout". Surface it as the actionable thing it is -
        // the backend also refuses to start a Connect session with no saved
        // credentials, so reaching this means guacd wants something more
        // (a domain, or the account was rejected).
        client.onrequired = () =>
          setError(
            "The remote desktop is asking for different credentials. Check this host's saved RDP username and password (the pencil button on the Remote Management page)."
          );
        client.onstatechange = (state) => {
          if (state === GUAC_STATE_CONNECTED) setSessionReady(true);
        };
        client.onclipboard = (stream, mimetype) => {
          if (!mimetype.startsWith("text/")) return;
          const reader = new GuacamoleLib.StringReader(stream);
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
        // Params go here (not in the tunnel URL) - see the bare-URL comment
        // above. WebSocketTunnel turns this into the "?..." query string the
        // backend's session route parses.
        client.connect(`mode=connect&token=${encodeURIComponent(token)}&w=${width}&h=${height}`);

        const displayElement = display.getElement();
        const mouse = new GuacamoleLib.Mouse(displayElement);
        mouse.onmousedown = mouse.onmouseup = mouse.onmousemove = (state) => client.sendMouseState(state);
        guacMouseRef.current = mouse;

        // Attached to the display element, NOT document: Guacamole.Keyboard
        // calls preventDefault on every key it handles, so a document-level
        // listener swallows keystrokes for the WHOLE app - and it kept doing
        // so after navigating away from the session (confirmed live: the
        // keyboard was dead on the Remote Management page itself). Bound to a
        // focusable element instead, keys are only captured while the remote
        // screen actually has focus, and teardown() drops the handlers.
        displayElement.tabIndex = 0;
        const keyboard = new GuacamoleLib.Keyboard(displayElement);
        keyboard.onkeydown = (keysym) => client.sendKeyEvent(1, keysym);
        keyboard.onkeyup = (keysym) => client.sendKeyEvent(0, keysym);
        guacKeyboardRef.current = keyboard;
        // Clicking the screen focuses it, so typing goes to the remote machine
        // without the operator having to think about focus.
        displayElement.addEventListener("mousedown", () => displayElement.focus());

        const onPaste = (e: ClipboardEvent) => {
          const text = e.clipboardData?.getData("text");
          if (!text) return;
          const stream = client.createClipboardStream("text/plain");
          const writer = new GuacamoleLib.StringWriter(stream);
          writer.sendText(text);
          writer.sendEnd();
        };
        displayElement.addEventListener("paste", onPaste);
      } catch (err) {
        setError(`Connect failed to start: ${err instanceof Error ? err.message : String(err)}`);
      }
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

  // Keeps the remote screen matched to the viewer box's actual size. Shadow
  // sends its own resize over the data channel: the agent switches the real
  // console to the NEAREST supported display mode (ChangeDisplaySettingsEx)
  // and then scales the encoded video to these exact pixels, so the picture
  // always fills the box even when the console can't do this precise mode
  // (see remote-agent/PROTOCOL.md and ShadowSession.cs). Connect uses
  // Guacamole's own sendSize, which rides RDP's native Display Control
  // channel for a real, live, exact resize. Debounced since a resize drag
  // fires this dozens of times a second.
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
                {selectedOrgId && id && host?.deployment_id && (
                  <PowerMenu orgId={selectedOrgId} hostId={id} />
                )}
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

type PowerState = "poweredOn" | "poweredOff" | "suspended" | null;

// ESXi-console-style power controls for a host DeployCore itself deployed
// (has a linked VM). Deliberately NOT gated on the remote session being up:
// the whole point of "Power on" is to start a machine that's currently off
// so it can then be connected to, and "Reset" is for a hung one that can't
// be reached at all. Talks to the same hypervisor driver the Deployments
// page's own power buttons use (see managed_hosts.py's power endpoints).
function PowerMenu({ orgId, hostId }: { orgId: string; hostId: string }) {
  const [state, setState] = useState<PowerState>(null);
  // null = still loading; false = host has no controllable VM (hide entirely).
  const [available, setAvailable] = useState<boolean | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{ action: "off" | "reset"; label: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const base = `/organizations/${orgId}/managed-hosts/${hostId}/power`;

  const refresh = useCallback(async () => {
    try {
      const res = await api.get<{ power_state: PowerState }>(base);
      setState(res.power_state);
      setAvailable(res.power_state !== null);
    } catch {
      setAvailable(false);
    }
  }, [base]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // A hypervisor power op takes a few seconds to settle (and a graceful
  // guest shutdown longer) - re-poll a couple times so the indicator catches
  // up on its own rather than needing a manual refresh.
  const repoll = useCallback(() => {
    setTimeout(refresh, 2500);
    setTimeout(refresh, 7000);
  }, [refresh]);

  const run = useCallback(
    async (action: "on" | "off" | "reset", hard = false) => {
      setBusy(action);
      setErr(null);
      setOpen(false);
      try {
        const res = await api.post<{ power_state: PowerState }>(`${base}/${action}`, action === "off" ? { hard } : {});
        setState(res.power_state);
        repoll();
      } catch (e) {
        setErr(e instanceof ApiError ? e.message : "Power action failed.");
      } finally {
        setBusy(null);
      }
    },
    [base, repoll]
  );

  if (!available) return null;

  const on = state === "poweredOn";
  const dot = on ? "bg-emerald-500" : state === "suspended" ? "bg-amber-500" : "bg-neutral-400";

  return (
    <div className="relative">
      <button
        className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-1.5 py-0.5 text-xs hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
        title={`Power controls — currently ${state ?? "unknown"}`}
        disabled={!!busy}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
        <Power size={12} strokeWidth={1.75} />
        Power
        <ChevronDown size={11} strokeWidth={1.75} />
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-50 mt-1 w-48 rounded-md border border-neutral-200 bg-white py-1 text-xs shadow-lg dark:border-neutral-700 dark:bg-neutral-900">
            <PowerItem icon={<Power size={12} strokeWidth={1.75} />} label="Power on" disabled={on} onClick={() => run("on")} />
            <PowerItem icon={<PowerOff size={12} strokeWidth={1.75} />} label="Shut down guest" disabled={!on} onClick={() => run("off", false)} />
            <PowerItem icon={<PowerOff size={12} strokeWidth={1.75} />} label="Power off (hard)" disabled={!on} danger onClick={() => setConfirm({ action: "off", label: "Power off (hard)" })} />
            <PowerItem icon={<RotateCcw size={12} strokeWidth={1.75} />} label="Reset" disabled={!on} danger onClick={() => setConfirm({ action: "reset", label: "Reset" })} />
          </div>
        </>
      )}

      {err && <span className="absolute right-0 top-full z-50 mt-1 whitespace-nowrap text-[11px] text-red-500">{err}</span>}

      <ConfirmDialog
        open={!!confirm}
        title={confirm?.label ?? ""}
        message={
          confirm?.action === "reset"
            ? "Hard-reset this machine now? This is the hypervisor's own reset - like a physical reset button, so any unsaved work in the running OS is lost."
            : "Force this machine off now? This is an immediate power cut, not a graceful shutdown - unsaved work is lost."
        }
        confirmLabel={confirm?.label ?? "Confirm"}
        onConfirm={() => {
          if (confirm?.action === "reset") run("reset");
          else run("off", true);
          setConfirm(null);
        }}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}

function PowerItem({
  icon,
  label,
  disabled,
  danger,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  disabled?: boolean;
  danger?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-40 ${
        danger ? "text-red-600 dark:text-red-400" : ""
      }`}
      disabled={disabled}
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  );
}
