# Native Remote Management Agent — replacing RustDesk

Plan only — nothing in this document has been implemented yet. Decisions
already made with the user before writing this:

- **Connect (RDP) mode**: Apache Guacamole (`guacd` + FreeRDP), not IronRDP,
  not a hand-rolled RDP client.
- **Shadow resolution**: bundle a third-party open-source IDD (Indirect
  Display Driver) rather than a view-only-scaling v1 or a VMware-Tools spike.

Everything else below follows from those two calls plus what's already true
of this codebase (read directly, not assumed): every managed host is an
ESXi/vSphere VM (`backend/app/hypervisors/` has exactly one driver, `esxi.py`)
with a VMware SVGA virtual GPU (no hardware H264 encoder), every agent
connects **outbound only** (no inbound ports on the managed host, ever - the
whole point of `RELAY_PORTS`' own docstring), and the existing WiX/.msi +
PowerShell + idempotent-Scheduled-Task installer lineage already correctly
solves Session-0/UAC/mid-install-reboot problems that have nothing to do with
RustDesk itself - that lineage is kept, only the payload changes.

## Why two backends, not one

"Shadow" and "Connect" are different problems wearing the same UI:

- **Shadow** = look at / control whatever's already on the console, no
  separate login, unattended. There's no existing native protocol for this on
  plain Windows 11 (real multi-session RDP Shadow is an RDS/Server feature -
  the same Server-vs-11 gap the last conversation covered) - this half
  genuinely needs custom capture/encode/transport code.
- **Connect** = a real, separate Windows login, own desktop session. Windows
  already has a native, mature protocol for exactly this - RDP, served by
  `TermService` on every SKU including Windows 11. Reinventing that would be
  pure waste; the job here is reusing it well, not replacing it.

One agent, one installer, one browser page - but two independent pipelines
underneath, each reusing the correct existing tool for its own job.

---

## 1. Shadow mode (mirror the active session)

**Pipeline:** DXGI capture → H264 encode → WebRTC → browser `<video>`.

- **Capture + encode**: an `ffmpeg` child process using the `ddagrab` input
  (wraps DXGI Desktop Duplication - no hand-written COM/DXGI interop needed)
  piped into an H264 encoder. Plan for **software `libx264` (`ultrafast` +
  `zerolatency`)** as the real default, not hardware encode - these are ESXi
  VMs with an SVGA virtual GPU, no NVENC/QuickSync/AMF available. A modern
  vCPU handles 1080p30 x264 ultrafast fine, but this is the single biggest
  technical unknown in the whole plan (see Phase 0 spike below) - flagging it
  now rather than assuming hardware encode "just works" the way it would on
  physical hardware.
- **Transport**: WebRTC via **SIPSorcery** (pure C#, no native deps) on the
  agent side, the browser's native `RTCPeerConnection` on the other. Buys us,
  for free, exactly the things RustDesk's own hbbs/hbbr relay model made
  fragile: DTLS-SRTP encryption, ICE-negotiated NAT traversal (host candidates
  connect **directly** on a LAN - matches the existing "every host is reached
  over a LAN" design note - with TURN only as fallback), and congestion
  control. `ffmpeg`'s raw H264 Annex-B stdout is parsed into NAL units and fed
  directly into SIPSorcery's RTP video track (a documented SIPSorcery pattern
  - not something to design from scratch).
- **Input**: an `RTCDataChannel` carries a small custom message set - mouse
  move/down/up/wheel, key down/up, clipboard get/set, monitor list/select,
  Ctrl+Alt+Del. Applied via `SendInput` (standard Win32 call). Ctrl+Alt+Del
  specifically needs `SendSAS` gated by the `SoftwareSASGeneration` policy,
  called from a SYSTEM-context process - a solved problem (every remote-access
  tool does this), but real setup work, not free; called out explicitly so it
  doesn't get "discovered" late.
- **Resolution** (the decided approach): the agent asks the bundled IDD
  driver's virtual monitor to switch to the **exact** width/height the
  browser's viewport reports - no "nearest supported mode" heuristic anywhere,
  because our own driver defines its own mode list. `ffmpeg`'s capture
  restarts internally to match the new size; this is invisible to the WebRTC
  layer - **the `RTCPeerConnection` itself is never torn down**, only the
  video source feeding it. That's the actual fix for the whole "WebSocket
  already CLOSING/CLOSED" churn class of bug: there's no renegotiation to
  churn in the first place. This also means `RemoteSession.tsx`'s
  `nearestResolution`/`COMMON_RESOLUTIONS`/dedup-timing logic can be deleted
  outright, not ported - the exact-size request just works.
- **IDD driver**: bundle + sign an existing open-source project rather than
  write a WDDM driver from zero (kernel-mode driver work is real, ongoing
  surface area - not worth reinventing when maintained options already exist
  for exactly this use case). Candidates to evaluate at Phase 4:
  `itsmikethetech/Virtual-Display-Driver` (built specifically for arbitrary
  custom resolutions) or Microsoft's own `IndirectDisplay` sample driver
  lineage. Needs either test-signing mode (`bcdedit /set testsigning on` - one
  line in the same idempotent install script) or a real EV/attestation
  signature for a fleet that shouldn't run in test-signing mode - a real,
  visible tradeoff to decide explicitly at that point, not bury as an
  assumption now.
- **Multi-monitor**: enumerate real + virtual outputs; default to the IDD
  virtual monitor, let the operator pick if more than one exists.

---

## 2. Connect mode (native RDP session)

**Pipeline:** browser (`guacamole-common-js`) → DeployCore backend (Guacamole
tunnel, hand-rolled - a few hundred lines, no new runtime) → `guacd` (Apache's
daemon, official image, wraps FreeRDP) → tunneled RDP → target's own
`TermService`.

- **Nothing to build for the RDP protocol itself** - Windows' own RDP server
  already exists on every SKU. The agent's only job at enroll time is making
  sure it's reachable: `fDenyTSConnections=0` + the firewall rule, the same
  idempotent-PowerShell pattern already used throughout
  `remote_agent_install.ps1`.
- **guacd**: official `guacamole/guacd` Docker image, added to
  `docker-compose.yml` the same way `rustdesk` is today - internal-only,
  reachable just by the `api` container, no published port (same shape as
  `rustdesk:21114` being internal-only today).
- **Browser client**: `guacamole-common-js` (Apache-licensed) - a
  `Guacamole.Client` bound to a `Guacamole.WebSocketTunnel` pointed at our own
  `/api/remote/session/{host_id}` route. Handles canvas rendering, input
  capture, and clipboard redirection out of the box - no protocol work on our
  side beyond the tunnel itself.
- **Reachability without inbound ports on the target** (the real design
  problem here, since plain Guacamole assumes `guacd` can dial the RDP host
  directly, which breaks the existing "agent only ever dials out" model): the
  agent opens a loopback connection to `127.0.0.1:3389` on receipt of a
  "start RDP tunnel" command over its already-open control WebSocket, and
  pumps bytes between that and a stream multiplexed over the same channel.
  Backend-side, a small per-session local TCP listener accepts `guacd`'s
  outbound RDP connection and pumps its bytes to/from that same tunneled
  stream. `guacd` never knows the real host is tunneled - it just sees an
  ordinary reachable RDP endpoint at `127.0.0.1:<ephemeral port>`. This is the
  one genuinely new piece of plumbing Connect mode needs, and it's small (a
  byte-pump, not a protocol).
- **Resolution**: RDP's own Dynamic Display Resolution virtual channel
  (MS-RDPEDISP), exposed by Guacamole as `"resize-method": "display-update"`.
  Real, exact, live resizing, natively - this is why the IDD driver only
  matters for Shadow mode, not Connect. Zero custom work here.
- **Credentials**: `ManagedHost.rdp_username` / `rdp_password_encrypted`
  carry over completely unchanged - guacd takes them directly as RDP
  connection parameters. Strict UX upgrade from today's "copy these into a
  Shadow session and paste them yourself" to a real auto-authenticated login,
  with no data-model change at all.

---

## 3. Shared agent & backend plumbing

**Agent**: one Windows service, **C#/.NET 8** (matches the existing
`remote-agent/tray/` app's language - continuity, not a new toolchain;
SIPSorcery is the mature pure-C# WebRTC option, and Win32 P/Invoke from C# is
the best-trodden path for `SendInput`/DPAPI/service work). Same install
lineage as today (WiX `.msi` + PowerShell one-liner + idempotent
ONSTART-triggered Scheduled Task) - just installing `DeployCoreAgent.exe` +
`ffmpeg.exe` + the IDD driver installer instead of the stock RustDesk `.msi`
and all the branding-suppression steps that only existed because RustDesk was
a foreign product. The tray app (`remote-agent/tray/Program.cs`) needs **no
changes at all** - it was always cosmetic and never touched RustDesk
internals.

One persistent outbound `wss://<server>/api/remote/agent-control` connection,
authenticated by a per-host credential minted at enroll (replacing the
"RustDesk permanent password" concept with e.g. an HMAC secret, stored via
Windows DPAPI - standard, no crypto to write ourselves). This single
connection multiplexes, by session id: heartbeat/presence, Shadow's SDP+ICE
signaling, and Connect's tunneled RDP byte-stream. Routed through the **same
Caddy origin** DeployCore already serves on (`/api/remote/agent-control`
alongside the existing `/api/*` paths) - deliberately reusing the hard-won
single-origin lesson this codebase already paid for three times over (see
`remote_desktop.py`'s `public_url_for()` docstring: mixed content, then a
separate-port cert-trust dead end, then a wrong sub-path, before landing on
"same origin, same Caddy, path-based routing"). There is no separate origin
for anything in this design, so that whole category of bug can't recur.

**Backend** (extends the existing FastAPI app, reuses existing patterns
rather than parallel new ones):
- `WS /api/remote/agent-control` - the agent's persistent connection.
- `WS /api/remote/session/{host_id}` - the operator's browser connection,
  authenticated by the normal DeployCore session/JWT + `require_role`
  (org-scoped, identical to every other `ManagedHost` route today).
- Redis pub/sub (already in `docker-compose.yml`, already used for user
  sessions in `security/sessions.py`) bridges the two WebSockets when they
  land on different worker processes - reuses infrastructure already in the
  stack rather than requiring sticky sessions.
- `ManagedHost`: drop `rustdesk_id` / `rustdesk_key_encrypted`, add
  `agent_key_encrypted` (the enroll-time credential). `rdp_username` /
  `rdp_password_encrypted` are untouched.
- Delete entirely once migrated: `remote_desktop.py`'s admin-login /
  address-book / share-token dance, the `rustdesk` container, and Caddy's
  `/webclient2`, `/webclient-config`, `/ws/id`, `/ws/relay`, `/api/shared-peer`
  blocks plus the whole `RUSTDESK_ALT_ACCESS_PORT` mechanism (which only
  existed to patch around hbbs advertising one fixed relay hostname - moot
  once we own the signaling protocol).

**Frontend** (`RemoteSession.tsx` rewritten, no more iframe):
- Shadow: `RTCPeerConnection` + `<video>` (hardware-decoded by the browser,
  free) + `RTCDataChannel` for input (DOM pointer/keyboard listeners → our
  message protocol).
- Connect: `guacamole-common-js`'s `Guacamole.Client` + `Guacamole.WebSocketTunnel`.
- Toolbar / fullscreen / credentials panel / Reconnect all carry over as UI
  concepts - just wired to our own protocol instead of RustDesk's `setByName`
  global and the rustdesk-api share-token flow. Every localStorage-patching /
  same-origin-iframe-internals hack in the current file disappears, because
  we own the whole page instead of embedding a foreign one.

**New infra**: one `coturn` container (official image, STUN/TURN for Shadow's
rare non-LAN NAT fallback - needs a published UDP port range, same category
as today's `RELAY_PORTS` setup-banner note) and one `guacd` container
(official image, internal-only, no published port).

---

## 4. Explicitly not being built (stated honestly, not hidden)

- No custom video codec - H264 via `ffmpeg`, most likely software `libx264`
  given ESXi's virtual GPU has no hardware encoder.
- No custom NAT traversal/relay - ICE/STUN/TURN via `coturn`.
- No custom encryption - DTLS-SRTP (WebRTC) for Shadow, RDP's own TLS/NLA for
  Connect, both free from the reused libraries.
- No hand-written kernel driver from scratch - bundle + sign an existing
  open-source IDD project.
- No hand-rolled RDP client - Guacamole/FreeRDP do the entire protocol.
- No cross-platform agent (Linux/macOS) in v1 - every current managed host is
  a Windows ESXi VM.
- No audio redirection, drive redirection, session recording, or
  multi-viewer/collaboration in v1 - not in today's feature set either;
  Guacamole already supports most of these cheaply if ever wanted later, so
  they're a small future add, not a v1 build item.

---

## 5. Migration plan

0. **Spike**: `ffmpeg ddagrab` → SIPSorcery → browser `<video>`, one
   throwaway ESXi VM, no backend integration. De-risks the one real unknown
   (software x264 latency/CPU on a vCPU) before committing further.
1. **Backend plumbing**: new WS routes, Redis bridging, additive
   `ManagedHost` migration (keep `rustdesk_*` columns until full cutover),
   `coturn` + `guacd` added to `docker-compose.yml` alongside (not replacing)
   `rustdesk`.
2. **Agent, Shadow path**: new C#/.NET agent binary, same WiX/.msi/installer
   lineage with a new payload, shipped behind a flag - existing
   RustDesk-enrolled hosts untouched.
3. **Connect/RDP path**: agent-side tunnel + backend byte-pump + `guacd`
   wiring + `guacamole-common-js` frontend integration, same test VM.
4. **IDD driver**: bundle/sign the chosen open-source driver, wire the
   resolution-change handler to it, delete the `nearestResolution`/snapping
   heuristics from the frontend.
5. **Cutover**: new enrollments use the new agent exclusively; existing hosts
   keep working on RustDesk until re-enrolled (same idempotent
   re-run-the-install-command story as today - nothing new to learn
   operationally).
6. **Teardown**: once every host is migrated, delete the `rustdesk`
   container, `remote_desktop.py`'s RustDesk module, and the Caddy RustDesk
   blocks.

---

## 6. Risks called out explicitly

- **Software encode cost on ESXi VMs** - the biggest real unknown; Phase 0
  exists specifically to answer this before more work is built on top.
- **IDD driver signing** - test-signing mode is a one-line, but visible,
  change to every managed host; a real EV/attestation-signed driver is a
  bigger, but invisible, alternative. Worth deciding explicitly at Phase 4,
  not defaulting into either silently.
- **Ctrl+Alt+Del / secure-desktop input** needs `SendSAS` + policy setup -
  solved elsewhere, but real work, not free.
- **`guacd` is one more daemon to operate** - same operational shape as
  today's `rustdesk` container, trading a small, single-maintainer project
  for a much larger, more widely audited one (Apache Guacamole).
