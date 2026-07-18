# DeployCore Remote Management Agent

The agent is DeployCore's own software, end to end - no RustDesk, no
third-party remote-desktop product wrapped and rebranded. See
[`PROTOCOL.md`](PROTOCOL.md) for the exact wire protocol and
[`../docs/remote-agent-native-plan.md`](../docs/remote-agent-native-plan.md)
for the full design rationale.

> **Status**: compiles clean (CI is green) and has been through real
> end-to-end testing on an actual ESXi-hosted Windows VM, not just CI - see
> the top-level [README's Remote Management section](../README.md#remote-management)
> for what that testing found and fixed, including Shadow now working
> whether or not anyone is logged into the target machine. See
> `agent/README.md` for what's still genuinely unverified below that
> surface (e.g. non-US keyboard layouts, RTP pacing jitter).

## Layout

- **`agent/`** - the actual Windows service (`DeployCoreAgent.exe`): capture/
  encode/WebRTC for Shadow mode, the RDP tunnel for Connect mode, the
  persistent control-channel client. This is the real product.
- **`tray/`** - a small, cosmetic tray-icon companion (unrelated to the
  RustDesk-era version's need to hide a foreign product's own tray icon -
  this one's just for a visible "DeployCore is managing this machine" icon).
  Needed **no changes** for the native rewrite; it never touched RustDesk
  internals.
- **`wix/`** - the WiX v4 source for `DeployCoreRemoteAgent.msi`, a thin
  wrapper that drops the install script + `DeployCoreAgent.exe` + `ffmpeg.exe`
  into Program Files and runs the script once.
- **`PROTOCOL.md`** - the control-channel and WebRTC data-channel wire
  protocol, authoritative for both the agent and the backend.

## The one source of truth: the install script

All install logic lives in **`backend/app/services/remote_agent_install.ps1`**
(kept there, not here, so `GET /api/remote/install-script` can serve it
directly with this instance's URL baked in). It:

1. Fetches this instance's TURN config (for Shadow's ICE server list) using
   the enroll token.
2. Installs `DeployCoreAgent.exe` + `ffmpeg.exe` (bundled in the `.msi`, or
   downloaded from the `agent-latest` GitHub release for the one-liner path).
3. Enables Remote Desktop (`fDenyTSConnections=0` + the firewall rule) -
   Windows' own RDP server does the entire job for Connect mode, nothing
   else to install for it.
4. Enrolls with DeployCore (server mints `agent_key`, the one and only
   credential in this whole system - see `PROTOCOL.md`'s "Why no password"
   section) and writes it, ACL-restricted, to
   `C:\ProgramData\DeployCore\agent-config.json`.
5. Installs `DeployCoreAgent.exe` as the `DeployCoreRemoteAgent` Windows
   service.

Delivered two ways, both using that same script - see the top-level
[README's Remote Management section](../README.md#remote-management) for the
actual commands.

**Dramatically shorter than the RustDesk-based version it replaces.** An
entire category of problems that script had to work around - a foreign
installer's own shortcuts/tray/Add-Remove-Programs entry, a UAC-hang
workaround for someone else's `--install-service` flag, reading an ID/password
back out of a running process over IPC - simply doesn't exist when the thing
being installed is DeployCore's own, from a service name to a config format
DeployCore chose itself.

## Building the `.msi` (automatic)

Built on `windows-latest` by `.github/workflows/build-agent-msi.yml`, on every
push that touches the agent, the install script, or `wix/`: publishes
`DeployCoreAgent.exe` as a self-contained single-file `win-x64` build,
downloads a static `ffmpeg.exe` (BtbN's builds, pinned tag - see the workflow
for the exact one), smoke-tests the `.msi`, and publishes both the `.msi` and
a `DeployCoreAgent.zip` (the one-liner path's download fallback) to a rolling
**`agent-latest`** pre-release.

DeployCore's `api` container auto-fetches the `.msi` on startup and registers
it as the global "Remote Agent" App Asset - see
`backend/app/services/remote_agent_seed.py`. To disable auto-fetch
(air-gapped installs), set `REMOTE_AGENT_MSI_URL=` empty and upload the `.msi`
as a global App Asset yourself, then **Set as agent**.

## Resolution and Ctrl+Alt+Del are both real today

Neither is a stub:

- **Resolution**: Shadow changes the VM's actual display resolution via the
  standard `ChangeDisplaySettingsEx` API (`Win32Interop.TrySetNearestResolution`),
  snapping to the closest mode the adapter actually supports - the same
  mechanism (and the same real constraint) the RustDesk-based version used,
  just computed against this machine's real, enumerated mode list
  (`EnumDisplaySettings`) instead of a hardcoded guess list. `ffmpeg`'s own
  `-vf scale` filter still resamples to the exact requested size on top of
  that, so the final video is always pixel-exact regardless of how close the
  underlying mode switch landed.
- **Ctrl+Alt+Del**: a real toolbar button in `RemoteSession.tsx`, in both
  Shadow (the agent's `SendSAS`) and Connect (the standard Guacamole/FreeRDP
  Ctrl+Alt+Del keysym sequence).
- **Shadow without anyone logged in**: `SessionCapture.cs` launches the
  capture process into the active console session using the real user's
  token when one exists (including a locked session - the token survives a
  lock), or a SYSTEM token retargeted to that session via
  `SetTokenInformation(TokenSessionId)` when nobody has ever logged in,
  aimed at the Winlogon desktop instead of the user's own. See that file's
  own doc comment for the one case this doesn't yet cover (a locked, not
  logged-out, session momentarily showing Winlogon too).

## What's still a stub, on purpose

- **Virtual display driver** (exact *arbitrary* resolution, not just the
  nearest supported mode): not yet bundled. `Install-VirtualDisplayDriver` in
  the install script and `IVirtualDisplay` in the agent are clearly-marked
  seams; a specific open-source IDD project still needs to be chosen,
  bundled, and signed (see the plan doc's "IDD driver" section for the
  candidates under consideration). The nearest-supported-mode switching
  above works today without it.
- **Hardware video encoding**: the agent uses software `libx264` via
  `ffmpeg`, not GPU-accelerated encode - these are ESXi VMs with only a
  VMware SVGA virtual GPU, so there's no hardware encoder to use anyway.
