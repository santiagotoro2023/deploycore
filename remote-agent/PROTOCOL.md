# Native agent wire protocol (v1)

Replaces RustDesk's own protocol entirely. Two independent channels - keep
them independent, don't let one leak into the other:

1. **Control channel** (agent ↔ DeployCore backend) - persistent, low-volume,
   goes through the backend. Only job: authenticate the agent, tell it when a
   session starts/ends, relay the Shadow WebRTC handshake, and pump Connect's
   RDP bytes. The backend never speaks WebRTC or RDP itself - it's a dumb
   relay for both.
2. **Data channel** (agent ↔ browser, direct WebRTC) - everything that
   happens *during* a Shadow session (video, input, resize, clipboard) goes
   here, peer-to-peer, once the handshake completes. The backend never sees
   this traffic at all - not a scaling nicety, the actual point: on the LAN
   this app is built for, this is a direct connection, not a relay hop.

## 1. Control channel

`wss://{server}/api/remote/agent-control`, opened by the agent, kept open for
the agent's whole lifetime (reconnect with backoff on drop).

**Auth at the HTTP upgrade**, not a first message - reject before `accept()`:
- `X-Enroll-Token`: the host's permanent identifier (`ManagedHost.enroll_token`).
- `X-Agent-Key`: the secret minted by the server at enroll time (replaces
  RustDesk's "permanent password" - same purpose, server-generated instead of
  agent-generated, see `POST /api/remote/enroll/{enroll_token}`).

Invalid credentials → close with code 4401, no message exchange at all.

**Text frames are JSON**, one object per frame, always tagged `"type"`:

| type | direction | fields | meaning |
|---|---|---|---|
| `heartbeat` | agent→server | - | every 20s; server bumps `last_seen_at` |
| `session_start` | server→agent | `session_id`, `mode` (`"shadow"` \| `"connect"`) | a browser opened a session |
| `session_end` | server→agent | `session_id` | browser disconnected or asked to stop |
| `signal` | both | `session_id`, `kind` (`"offer"`\|`"answer"`\|`"ice"`), `sdp`?, `candidate`?, `sdpMid`?, `sdpMLineIndex`? | Shadow only - SDP/ICE relay, verbatim, backend never inspects `sdp`/`candidate` contents |

The **agent creates the SDP offer** (it owns the video track), the browser
answers - not the more common "browser offers" pattern, because here the
agent is the one with media to add.

**Binary frames** are the Connect-mode RDP tunnel, both directions: first 16
bytes = `session_id` (raw UUID bytes), remainder = raw bytes to pump verbatim
in either direction. No framing beyond that - it's a byte pipe, not a
protocol. On `session_start` with `mode: "connect"`, the agent opens a
loopback TCP connection to `127.0.0.1:3389` and starts pumping; on
`session_end` or a closed loopback socket, it stops.

## 2. Data channel (Shadow only, agent ↔ browser directly)

One `RTCDataChannel` alongside the video track. JSON per message, tagged `"t"`:

| t | direction | fields | meaning |
|---|---|---|---|
| `mousemove` | browser→agent | `x`, `y` (px, relative to the current virtual resolution) | `SendInput` absolute move |
| `mousedown` / `mouseup` | browser→agent | `button` (0/1/2) | |
| `wheel` | browser→agent | `dy` | |
| `keydown` / `keyup` | browser→agent | `code` (`event.code`, physical layout-independent key, never `event.key`) | |
| `cad` | browser→agent | - | Ctrl+Alt+Del via `SendSAS` |
| `clipboard` | both | `text` | plain-text clipboard sync |
| `resize` | browser→agent | `w`, `h` | exact target resolution - see below |

`resize` never renegotiates the `RTCPeerConnection`. The agent restarts only
its capture (`ffmpeg`) once the IDD driver's virtual monitor reports the new
mode - the peer connection and data channel stay up throughout. This is the
actual fix for the old "WebSocket already CLOSING/CLOSED" churn: there is no
teardown left to churn.

## Why no password, no "being controlled" notice

The control channel's `X-Agent-Key` is the only credential anywhere in this
system, and the operator never sees or types it - it's server-minted at
enroll time and never leaves the backend/agent. There is nothing for a
session to prompt for. Same reasoning DeployCore already applied to RustDesk
(`allow-hide-cm=Y`, "permitted because we use a permanent password") - carried
over deliberately, not a new decision.
