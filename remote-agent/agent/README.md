# DeployCoreAgent

The native Windows service behind DeployCore's Remote Management: one .NET 8
process, installed as the `DeployCoreRemoteAgent` Windows service by
`backend/app/services/remote_agent_install.ps1`, that implements the wire
protocol in `remote-agent/PROTOCOL.md` end to end - a persistent control
channel back to DeployCore, Shadow mode (desktop mirroring over WebRTC), and
Connect mode (a tunneled RDP byte-pipe to `guacd` on the backend). No
RustDesk anywhere in this - this project *is* the "own agent" the
RustDesk-based version was replaced with.

Read `remote-agent/PROTOCOL.md` first; this file just covers building,
running, and this specific implementation's rough edges.

## Build

```
dotnet publish remote-agent/agent/DeployCoreAgent.csproj -c Release -r win-x64 \
  --self-contained true -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true
```

This is the exact invocation `.github/workflows/build-agent-msi.yml` uses -
Windows-only (real Win32 P/Invoke throughout - `SendInput`, `SendSAS`, the
clipboard API, DPAPI), self-contained single-file so a freshly-provisioned
VM needs no separate .NET runtime install. `ffmpeg.exe` is a separate
download (see the workflow) that CI stages next to the published exe - it is
not part of this project and is expected next to `DeployCoreAgent.exe` at
runtime (or on `PATH`).

## Config file contract

Read from `%ProgramData%\DeployCore\agent-config.json`
(`C:\ProgramData\DeployCore\agent-config.json` on a normal install) at
startup - written by `remote_agent_install.ps1`, step 6. Shape as the
installer writes it, on a first run:

```json
{
  "serverUrl": "https://...",
  "enrollToken": "...",
  "agentKey": "...",
  "turnHost": "...", "turnPort": 3478, "turnUsername": "...", "turnPassword": "...",
  "virtualDisplay": false
}
```

On first read, `AgentConfig.LoadAndProtect` re-protects `agentKey` with DPAPI
(`ProtectedData.Protect`, `DataProtectionScope.LocalMachine` - this runs as
SYSTEM with no interactive profile, so `CurrentUser` scope isn't an option)
and rewrites the file with `agentKeyProtected` (base64) in its place, with
the plaintext field gone entirely:

```json
{
  "serverUrl": "https://...",
  "enrollToken": "...",
  "agentKeyProtected": "<base64 DPAPI blob>",
  "turnHost": "...", "turnPort": 3478, "turnUsername": "...", "turnPassword": "...",
  "virtualDisplay": false
}
```

Every run after that reads `agentKeyProtected` and unprotects it in memory
only. This is a real, load-bearing security step, not decoration:
`C:\ProgramData` is world-readable by default even though the installer also
tightens this specific file's ACL with `icacls` right after writing it - DPAPI
is the second line of defense that survives even if that ACL is ever
loosened later.

## Shape of the code

One file per concern, no framework beyond what
`Microsoft.Extensions.Hosting`/`.UseWindowsService()` already gives for free:

- `Program.cs` - host bootstrap + `AgentWorker : BackgroundService`, the
  entire service lifecycle.
- `AgentConfig.cs` - config load + the DPAPI migration above.
- `Win32Interop.cs` - every raw P/Invoke declaration in one place:
  `SendInput` (mouse/keyboard), `SendSAS` + the `SoftwareSASGeneration`
  policy helper, the raw clipboard API, and the DPAPI wrapper.
- `ControlChannelClient.cs` - the persistent `wss://.../api/remote/agent-control`
  connection: auth headers, reconnect-with-backoff (3s/6s/12s/30s), 20s
  heartbeat, JSON message dispatch, and the 16-byte-session-id binary framing
  for Connect's tunnel.
- `ShadowSession.cs` - ffmpeg capture, Annex-B NAL parsing, the SIPSorcery
  peer connection + data channel, resize handling.
- `ConnectTunnel.cs` - the loopback-to-3389 byte pump for Connect mode.
- `IVirtualDisplay.cs` - the seam for a future real virtual-display driver;
  today's only implementation just logs (see `remote_agent_install.ps1`'s own
  `Install-VirtualDisplayDriver` stub, which is why `virtualDisplay` is
  always `false` in practice right now).

## Not yet verified on real hardware

**This code has not been compiled or run.** The environment it was written
in has no Windows, no .NET SDK, and no target machine - everything here is
written as carefully as possible against documented Win32/.NET/SIPSorcery
APIs, but "compiles" and "works" are both still open questions. The first
real test needs a Windows 11 or Windows Server VM with RDP enabled, plus a
browser session against a real DeployCore instance with TURN configured.

Specific things that are most likely to need a fix once it can actually be
built and run, roughly in order of how likely they are to need a change:

1. **SIPSorcery's exact API surface** (`ShadowSession.cs`). Method names,
   casing, and exact overloads (`createOffer`, `setLocalDescription`,
   `addTrack`, `SendVideo`, the `RTCConfiguration`/`RTCIceServer` field
   names, the data channel `onmessage` signature, `RTCSdpType.answer`'s
   casing) are written from general familiarity with the library's 6.x line,
   not compiled against the actual restored package. A `dotnet build` on a
   real machine will surface any mismatches immediately and they should be
   easy, mechanical fixes - the *shape* of the pipeline (agent creates the
   offer, trickle ICE over the control channel, externally-encoded H264 fed
   into a video track) is the part that's actually load-bearing and unlikely
   to need to change.
2. **RTP timestamp pacing** in `ShadowSession.ReadNalUnitsAsync` - the full
   per-frame duration is passed on every NAL of a multi-NAL access unit
   (SPS/PPS/slice), not just the last one. Decodability doesn't depend on
   this (only smooth jitter-buffer pacing does), so video should still show
   up; if playback looks jittery, this is the first place to look. Comment
   at the call site names the exact upgrade (parse NAL header types, only
   advance the timestamp on the first VCL NAL of each picture).
3. **Keyboard layout dependence** (`Win32Interop.KeyEvent`) - `event.code` is
   mapped to Windows virtual-key constants, not hardware scancodes. Correct
   for a US layout on the target machine; a non-US layout would need a real
   DOM-code-to-PS/2-scancode table (`KEYEVENTF_SCANCODE`) for true
   physical-key independence. Named in a doc-comment at the call site.
4. **`ffmpeg` build/latency** - `gdigrab` (not `ddagrab`) is what's wired up,
   per this task's own scope; the design doc flags software x264 encode cost
   on an ESXi VM's virtual GPU as the single biggest real unknown in the
   whole native-agent plan, independent of anything in this file.
5. **`SendSAS`/`SoftwareSASGeneration`** (`Win32Interop.EnsureSoftwareSasGeneration`) -
   believed correct from MSDN's own description (SYSTEM-context caller +
   this policy value), not exercised live.
6. **A real backend gap, not an agent bug**: `ConnectTunnel` sends
   `session_end` back to the server when the local RDP socket closes (per
   PROTOCOL.md), but `backend/app/api/routes/remote_agent.py`'s control-channel
   receive loop, as read while writing this, has no handler for an
   agent-initiated `session_end` - only `heartbeat` and `signal`. The agent
   sends it anyway (harmless, forward-compatible); today the browser side
   would only find out a Connect session died some other way (e.g. its own
   WebSocket closing). Worth fixing backend-side at some point, but out of
   scope for this agent.
7. **Clipboard agent-to-browser sync** (`ShadowSession.ClipboardPollLoopAsync`)
   polls the local clipboard every 2s rather than reacting to a real
   `WM_CLIPBOARDUPDATE` notification, which would need a hidden message-only
   window + `AddClipboardFormatListener` - real plumbing for a "keep both
   ends in sync" nicety. Up to ~2s of extra latency on that one direction
   only; browser-to-agent clipboard sync is instant (a direct data-channel
   message).

Nothing here was left silently simplified - every shortcut above has a
comment at its call site naming the ceiling and the upgrade path.
