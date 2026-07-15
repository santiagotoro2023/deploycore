# DeployCore Remote Management Agent

The agent is a **stock, unmodified RustDesk client**, installed as a headless
Windows service and pointed at this DeployCore instance's own self-hosted
RustDesk server. There is no recompiled/forked RustDesk — the only
DeployCore-authored code is the install script and (optionally) a small tray
companion.

## The one source of truth: the install script

All install logic lives in **`backend/app/services/remote_agent_install.ps1`**.
It: fetches this instance's server config (relay address + public key) using the
enroll token, installs the stock RustDesk client silently, writes a headless
config (`hide-tray`, permanent-password unattended access), installs the
service, generates a local permanent password, and enrolls the machine back
with DeployCore.

It's delivered two ways, both using that same script:

1. **One-liner (easiest, no download).** The Remote Management tab shows a
   copy-paste command; the server serves the script with its own URL baked in
   (`GET /api/remote/install-script`):
   ```powershell
   powershell -ExecutionPolicy Bypass -Command "$env:DC_TOKEN='<enroll-token>'; irm <server>/api/remote/install-script | iex"
   ```

2. **The `.msi`** (`remote-agent/wix/`). A thin WiX wrapper that drops the same
   script and runs it once, passing `SERVERURL` + `ENROLLTOKEN` MSI properties:
   ```
   msiexec /i DeployCoreRemoteAgent.msi /qn SERVERURL="<server>" ENROLLTOKEN=<token>
   ```
   This is also the form the DeployCore deployment pipeline uses automatically
   (see `worker/tasks/provision.py`) when the agent is attached to a template as
   the global "Remote Agent" App Asset.

## Building the `.msi` (automatic)

The MSI can only be built on Windows (WiX), not on the Linux hosts DeployCore
otherwise uses, so `.github/workflows/build-agent-msi.yml` builds it on a
`windows-latest` runner. It runs **automatically on every push that changes the
agent** (the install script or `wix/`), smoke-tests the built MSI (installs it
with `SKIPRUN=1`, checks the payload landed, uninstalls), and publishes it to a
rolling **`agent-latest`** pre-release with a stable URL:

```
https://github.com/santiagotoro2023/deploycore/releases/download/agent-latest/DeployCoreRemoteAgent.msi
```

Pushing an `agent-v*` tag also cuts a normal versioned release.

**You don't need to upload it into DeployCore.** On startup the api container
auto-fetches that MSI and registers it as the global "Remote Agent" App Asset
(`config.remote_agent_msi_url`, `app/services/remote_agent_seed.py`), which is
what the tab's "Download .msi" button and the deployment pipeline both use. To
disable auto-fetch (air-gapped installs), set `REMOTE_AGENT_MSI_URL=` empty and
upload the `.msi` as a global App Asset yourself, then **Set as agent**.

> The MSI's post-install custom-action sequencing is the least-tested part
> (WiX v4 + a commit custom action). CI's smoke test catches authoring/
> sequencing breakage, but the very first real end-to-end install (which does
> reach a live DeployCore server) is still worth eyeballing on a throwaway VM.
> The PowerShell one-liner path depends on none of this and is the guaranteed
> install method.

## Branding — what's DeployCore, what still says "RustDesk"

We install the **stock** RustDesk client (no source recompile — that's the
deliberate call to avoid owning a Rust/Flutter build pipeline), then relabel
everything installer-level tweaks can reach.

**Shows as DeployCore:**
- The whole browser/operator experience.
- The notification-area **tray icon** (`.msi` installs it) — the DeployCore mark
  and name "DeployCore Remote Management Agent". This is the `remote-agent/tray/`
  companion app; the RustDesk tray itself is hidden (`hide-tray=Y`) since it
  can't be re-branded without a recompile.
- Add/Remove Programs entry — renamed to "DeployCore Remote Management Agent",
  publisher "DeployCore", DeployCore icon. Done by the install script's branding
  step + the MSI's `ARPPRODUCTICON`.
- The Windows service's display name in services.msc — "DeployCore Remote
  Management Agent".
- The `.msi` wrapper package name and icon.
- **No "being controlled" window** during a session — suppressed with
  `allow-hide-cm=Y` (permitted because we use a permanent password).

**The icon is the same mark everywhere** — the tray, Add/Remove Programs, and the
MSI icon are all drawn (by the tray app, GDI+, in CI) from the same DeployCore
mark as the browser favicon (`frontend/public/favicon.svg`) and the in-app logo.

**Still says "RustDesk"** (would need a source recompile to change):
- The `rustdesk.exe` process name in Task Manager.
- The `C:\Program Files\RustDesk` install folder.
- The Windows service's *key* name (`RustDesk`) — only the display name is
  relabeled.

These are low-visibility (you have to open Task Manager or browse Program Files),
and none are seen by the DeployCore operator in normal use.

**Note:** the tray icon and the custom `.ico` ship only with the `.msi` (CI
builds them); the copy-paste one-liner install path applies the name/publisher
/service branding and hides the CM window, but has no tray app or custom icon.
