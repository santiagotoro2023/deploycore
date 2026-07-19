<img src="docs/brand/deploycore-lockup.svg" alt="DeployCore" width="720" />

**Contents:** [Quickstart](#quickstart) · [First run, step by step](#first-run-step-by-step) ·
[Updating](#updating) · [HTTPS certificate](#https-certificate) ·
[Roles and multi-tenancy](#roles-and-multi-tenancy) ·
[Capabilities](#capabilities) · [API reference](#api-reference) ·
[Environment variables](#environment-variables) · [Development](#development) ·
[Uninstalling](#uninstalling) · [Known limitations](#known-limitations)

DeployCore turns "provision a Windows Server VM" from a manual, error-prone
routine into a few clicks. Point it at your ESXi hosts, upload a Windows
Server ISO once, define what a server should look like (disk layout, roles,
domain membership), and then create new servers on demand, each one a real,
fresh Windows Setup install, not a cloned image. It's built for MSPs and IT
teams running multiple customer environments side by side: every customer
gets their own isolated organization, its own hypervisors, its own templates,
its own audit trail, all inside one instance with role-based access control.

If you've used Foreman for Linux/PXE provisioning, this is the same idea,
narrowed to Windows Server and built around unattended answer files instead
of PXE.

## Quickstart

Requirements: Docker Engine + Docker Compose v2, and network access from
this host to your ESXi hosts.

```bash
git clone https://github.com/santiagotoro2023/deploycore.git deploycore
cd deploycore
./scripts/setup.sh
```

That's it. The script copies `.env.example` to `.env`, generates a secret
key, detects this host's own LAN IP and sets `APP_PUBLIC_URL` to it (see
below), and builds and starts the whole stack. Open `https://localhost`
and you'll land on a two-step setup wizard: name your instance, then create
your first administrator account. Everything else (database schema, etc.) is
handled automatically, including on every future update (see "Updating"
below).

The stack is fronted by a small built-in reverse proxy that terminates
HTTPS on port 443 and redirects any plain HTTP request (port 80) to it, so
your browser will warn you about an untrusted certificate the first time,
it's self-signed by default. See "HTTPS certificate" below for uploading a
real one. `http://localhost:5173` (the frontend directly, no TLS) still
works too, useful for local development.

One setting worth double-checking before you provision real VMs:
`APP_PUBLIC_URL` in `.env`. This is the address your ESXi guest VMs call back
to once Windows Setup finishes, it needs to be reachable from your customer
networks, not just from your own laptop. That value is baked as a literal
string into PowerShell commands that run *inside the guest itself*, so
`localhost` almost never works there, it'd resolve to the VM, not to
DeployCore. `scripts/setup.sh` sets this for you automatically (detects this
host's own LAN-facing IP and writes `http://<that-ip>:8000`), so most
installs never need to touch it by hand at all, but the auto-detected
address is only as good as the network `setup.sh` happened to run from: on a
multi-NIC host, or if your VMs actually land on a different network than the
one detected, edit `APP_PUBLIC_URL` in `.env` yourself and re-run
`docker compose up -d` to pick up the change. Keep it `http://`, on port
8000, not `https://`/443: that port is the `api` container exposed directly
(`8000:8000`), a separate path from the HTTPS reverse proxy that only fronts
the browser UI, on purpose, since a fresh Windows guest's default PowerShell
can't easily validate the proxy's self-signed certificate.

## First run, step by step

1. **Setup wizard.** Instance name, then your admin account (username,
   display name, password, optional email). This can only run once, a
   `409` protects it from running again on an already-configured instance.
2. **Branding (optional).** Settings page → give the instance a name and
   upload a logo if you want it to look like your own product instead of a
   generic dashboard.
3. **Add a hypervisor.** Hypervisors page → New hypervisor. Enter the ESXi
   endpoint and credentials, click **Test Connection** before saving so you
   catch a typo or bad password immediately instead of during a real
   deployment.
4. **Upload a Windows Server ISO.** ISO Assets page. Large files upload in
   chunks, so this works fine even on a slow connection, just leave the tab
   open.
5. **Check the disk layout.** Disk Layouts page. A sensible default already
   exists (EFI + MSR + a recovery partition placed mid-disk + the OS volume
   taking the rest), created automatically during setup. Most people never
   need to touch this.
6. **Create a template.** Templates page. This is the reusable "recipe" for
   a server: which ISO, which disk layout, CPU/RAM/disk size, network name
   (the ESXi/vCenter port group or vSwitch the VM's NIC attaches to, not a
   Windows network name), a local administrator username and password
   (a custom account created fresh on every deployment; the built-in
   Administrator account gets disabled automatically), optional domain
   join, and which Windows roles to install, picked from checkboxes (AD
   Domain Services, DNS, DHCP, IIS, Print Services, RD Session Host, DFS
   Namespaces, DFS Replication). Installing AD Domain Services installs the
   role binaries only, it does not promote the server to a domain
   controller or configure any GPOs/OUs, see "Known limitations" below.
7. **Deploy.** Deployments page → New deployment. Pick the template and
   hypervisor, give the new server a hostname and IP configuration, review
   the exact answer file that will be used, and deploy. Watch it happen
   live: VM creation, Windows Setup, role installation, all logged in real
   time on the deployment's page.

The Dashboard shows a "Getting started" checklist with these steps until
you've completed them, so you always know what's next.

If something fails partway through, the deployment's page has a
**Download full log** button, everything about that attempt (every stage,
every command, the full error and traceback) in one text file, the fastest
way to figure out what went wrong.

## Updating

Settings → **Updates** shows your current version and, if you're behind,
how many commits, refreshed automatically in the background every 5
minutes; **Check for update** forces that check right now instead of
waiting. Click **Update now** and DeployCore pulls the latest code from
GitHub, rebuilds, runs any new database migrations, and restarts itself,
automatically. A modal opens with a progress bar tracking each stage
(Pulling → Building → Restarting → Finalizing), and the page reloads
itself automatically once the update reports done. The app is only
unreachable for the last part of that, usually under a minute. Nothing in
your database is touched by an update itself.

When you're behind, a collapsible **What's new** list shows the actual
commit subjects the update would bring in, not just the count - the
updater computes `git log HEAD..origin/<branch>` alongside its usual
`commits_behind` check and stores the subject lines as a setting
(`pending_changelog`). After an update runs, **What the last update
changed** shows the same thing for what actually landed
(`git log <old HEAD>..<new HEAD>` at the moment the update finished,
stored as `last_update_changelog`) - persisted, so it's still there
whenever you next open Settings, not just in the instant the update
completed.

This works because a small dedicated `updater` container in the stack has
access to the Docker socket, i.e. it can tell your host's Docker daemon what
to do, the same way tools like Portainer or Watchtower work. That's a real
privilege (that container can, in principle, control anything else running
on the same Docker host), which is why it's worth understanding before you
rely on it: it's a reasonable trade-off for a self-hosted tool where you
already control the host, less so if you'd hand this instance to someone you
don't fully trust. Nothing needs to be configured for it to find this repo
on the host, either: it asks the Docker API for its own bind mount's source
path and uses that, so it works out of the box on both a fresh install and
an existing instance that's never touched this feature before. If that
lookup can't work in your specific Docker setup (a socket proxy that blocks
`docker inspect`, for example), the Settings page shows exactly why in its
error message, and setting `PROJECT_DIR` in `.env` to this repo's absolute
host path overrides the automatic lookup entirely. If you'd rather not run
it at all, remove the `updater` service from `docker-compose.yml` and
update by hand instead:

```bash
git pull
docker compose up -d --build
```

(same effect: `make update` runs exactly this)

Migrations always run automatically on `api` container startup regardless of
which update path you use, so there is never a separate manual migration
step.

## HTTPS certificate

Settings → **HTTPS certificate** (global admin only) shows whether the
instance is currently serving a self-signed certificate or an uploaded one,
and lets you switch between them.

By default DeployCore generates and serves a self-signed certificate, no
setup needed, but every browser will flag it as untrusted since no
certificate authority vouches for it. To get rid of that warning, upload a
certificate and matching private key (PEM format, unencrypted key) signed
by a CA your users' browsers already trust, e.g. one issued by Let's
Encrypt or your internal CA. It's picked up within a few seconds, no
restart required.

The self-signed certificate works no matter how you reach the instance,
`localhost`, a LAN IP, a port-forwarded public IP, a hostname, since it's
issued on the fly per request (Caddy's `on_demand` internal TLS) instead
of being generated once for a fixed name. The trade-off, spelled out in
Caddy's own docs, is that anyone who can reach the port can make it mint a
certificate for an arbitrary name; harmless for a self-hosted admin tool
behind your own firewall/router, but worth knowing if this instance is
wide open on the public internet, in which case uploading a real
certificate (or restricting network access) is the better long-term move
anyway.

If you ever need to go back, e.g. to test something, or because the
uploaded certificate expired, **Switch to self-signed temporarily** does
exactly that without discarding the uploaded certificate, and **Use this
certificate again** switches back to it, no re-upload needed.

**Remote Management's sessions need no separate step at all** - they're just
more WebSocket routes under DeployCore's own `/api/*`, on this same origin and
certificate, not a different port. That's a deliberate design carried over
from a real, load-bearing lesson learned the hard way on this app's old
RustDesk-based version, worth knowing about if you're touching this code: an
early version loaded the embedded web client from its own `http://<host>:21114`
URL, which browsers block as mixed content inside an HTTPS page; a later
version gave it a dedicated HTTPS port instead, which fixed that but meant a
*separate* certificate a browser has to trust separately - and a browser
flatly refuses to let you click through a certificate warning *inside* an
embedded frame at all, so anyone who hadn't visited that port directly first
got a silent, unexplained failure. The native agent's own design (see
`remote-agent/PROTOCOL.md`) applies that lesson from the start instead of
rediscovering it: `/api/remote/agent-control` and
`/api/organizations/{org_id}/managed-hosts/{host_id}/session` are ordinary
routes on this same origin, so whatever already lets a browser load the rest
of DeployCore - a real certificate, or having already clicked through the
self-signed warning once - covers Remote Management automatically, for
anyone who can already reach this instance at all.

If you'd rather skip that initial self-signed warning for the whole
instance (not specific to Remote Management), `https://<your-instance>/ca.crt`
downloads Caddy's own locally-generated Certificate Authority root; install
it as a trusted root once per machine (push it fleet-wide via a GPO startup
script, RMM, or MDM if you manage more than a couple) and that machine
trusts every certificate this instance issues, forever. Not needed at all
once a real uploaded certificate is in use.

This is handled by a small `proxy` service (Caddy) in front of the rest of
the stack: it terminates TLS on 443 and redirects 80 to it, forwarding
everything else to the frontend unchanged. The self-signed default starts
immediately and never depends on the database being reachable, only
switching to an uploaded certificate does: it watches the same `tls_mode`
setting the API writes to and reloads itself automatically when it
changes, the same polling pattern the `updater` container uses for
self-update (see above).

## Roles and multi-tenancy

Three roles, ordered `admin > operator > readonly`. A user has a
`global_role` (applies everywhere) and/or per-organization roles (Users page
→ assign org role). The effective role for a request is the higher of the
two for that organization. `none` (the default for a newly created user with
no explicit role) grants no access anywhere.

| Role | Can do |
|---|---|
| `readonly` | View everything in organizations they're scoped to |
| `operator` | Everything `readonly` can, plus: create/retry/bulk-create deployments, power on/shut down/power off a deployment's VM, create/edit/delete disk layouts, templates, ISO assets, and app assets, clone/export/import templates and disk layouts |
| `admin` | Everything `operator` can, plus: create/edit organizations, manage hypervisor hosts and webhooks (including credentials/secrets) and run their test buttons, delete a deployment record, edit organization/global settings, manage users, their global or org-role assignments, and deactivate/reactivate or permanently delete a user (global-admin only), delete an organization outright (global-admin only), rename the instance and manage its logo (global-admin only), configure M365 email and trigger backups/updates (global-admin only), upload/switch the HTTPS certificate (global-admin only) |

RBAC is enforced server-side on every route (a dependency resolves the
caller's effective role for the request's organization and returns `403`
below the floor), the UI hiding a button is a convenience, not the
enforcement point.

Every `Organization` is an independent tenant: its own hypervisors,
templates, disk layouts, ISO assets, app assets, deployments, webhooks,
settings, and audit log. There is no separate "MSP organization" entity, the instance
itself is identified by the `instance_name` (set during setup, editable
afterward) and "MSP admin" means any user with `global_role = admin`, who
can see and manage every organization. `DiskLayout`, `DeploymentTemplate`,
and `IsoAsset` can also be created with no organization (`global`), in which
case every organization inherits them read-only and can clone them into an
org-scoped copy.

## Remote Management

See and control an enrolled server or workstation's screen from the browser,
from anywhere — no file to download to connect, no VPN. Fully self-hosted and
**entirely DeployCore's own software end to end** - no RustDesk, no
third-party relay, nothing that isn't either DeployCore's own code or a
well-known open-source building block (WebRTC, Apache Guacamole/FreeRDP,
coturn) DeployCore controls the integration of. See
[`remote-agent/PROTOCOL.md`](remote-agent/PROTOCOL.md) for the exact wire
protocol and [`docs/remote-agent-native-plan.md`](docs/remote-agent-native-plan.md)
for the full design.

Two modes, both from the same enrolled agent:
- **Shadow** — mirrors the machine's active console live (unattended,
  no separate login), streamed over WebRTC straight from the agent to your
  browser. No password ever appears anywhere - the one credential in this
  whole system (`agent_key`) is minted by DeployCore at enroll time and never
  shown to an operator, matching a decision already made when this ran on
  RustDesk (`allow-hide-cm`, "permitted because we use a permanent
  password") - just with nothing left needing to be shown at all now, since
  there's no client-side password prompt to skip in the first place.
- **Connect** — a real, separate native RDP login (Windows' own
  `TermService`), reached through a self-hosted Apache Guacamole (`guacd` +
  FreeRDP), auto-authenticated with the saved RDP username/password. Live,
  exact resolution changes (RDP's own Display Control channel), not
  something DeployCore has to solve itself.

Both share a clipboard with your machine and support full-screen.

**It's automatic on install.** `coturn` (STUN/TURN, for the rare host that
isn't on this server's own LAN - Shadow's WebRTC path always tries a direct
connection first) and `guacd` (Apache Guacamole's daemon) ship in the
`docker compose` stack, and `scripts/setup.sh` generates the TURN secret and
points it at this host's detected address. A normal install brings it up with
no extra steps. The Remote Management tab shows a banner if anything still
needs attention.

**The one thing that can't be automated is network reachability**, and only
for a host that isn't on this server's own LAN (the common case for this app
needs nothing forwarded at all - Shadow's WebRTC negotiates a direct
connection, and Connect's RDP traffic is tunneled through the agent's own
outbound connection, never dialed into directly):

| Port | Protocol | Purpose |
|---|---|---|
| 3478 | TCP+UDP | STUN/TURN (coturn) |
| 49160–49200 | UDP | TURN relay range |

You set the public address in the app, not in files: **Settings → Remote
Management** takes a public IP or domain (defaults to your LAN IP) and applies
it immediately - nothing to restart, unlike the old RustDesk relay, since this
is just data read live by every new session rather than baked into a
container's own startup args.

**Enrolling a machine.** Either attach the "DeployCore Remote Management Agent"
App Asset to a template (every machine deployed from it enrolls automatically),
or, for an existing machine, click **Add host** and use the one-line PowerShell
command shown (easiest, nothing to download) or the silent `.msi`:

```
msiexec /i DeployCoreRemoteAgent.msi /qn SERVERURL="https://your-deploycore" ENROLLTOKEN=<token>
```

**The agent `.msi`.** DeployCoreAgent.exe (this project's own service - capture,
encode, WebRTC, and the RDP tunnel, see `remote-agent/agent/`) plus `ffmpeg.exe`,
installed as a background service with no foreign product to hide (there's
nothing here that isn't DeployCore's own, unlike the old RustDesk-based
version). Built automatically by GitHub Actions
(`.github/workflows/build-agent-msi.yml`) on every push that touches the
agent, and published to a rolling release. Direct download:
[**DeployCoreRemoteAgent.msi**](https://github.com/santiagotoro2023/deploycore/releases/download/agent-latest/DeployCoreRemoteAgent.msi).
DeployCore also **auto-fetches it on startup** and registers it as the global
"Remote Agent" App Asset, so the Remote Management tab's download button and the
auto-install-on-deploy path work with no manual upload. (Air-gapped? Set
`REMOTE_AGENT_MSI_URL=` empty and upload the `.msi` as a global App Asset
yourself, then **Set as agent**.) Full details in
[`remote-agent/README.md`](remote-agent/README.md).

> **Status:** compiles clean and has been through several rounds of real
> end-to-end testing on an actual ESXi-hosted Windows VM (not just CI) -
> several real bugs were found this way and fixed, including one where an
> earlier fix compiled, ran, and reported no error but turned out not to
> actually work (see below) - not everything here is "theorized," but not
> everything is "confirmed working" either; read this status honestly.
> CI (`.github/workflows/build-agent-msi.yml`) is green on every push that
> touches the agent.
>
> Resolution and Ctrl+Alt+Del are both real, working features today, not
> placeholders: Shadow changes the VM's actual resolution via the standard
> `ChangeDisplaySettingsEx` API, snapping to the closest mode the adapter
> supports. That call has a real constraint - it only succeeds when made
> from a thread attached to the interactive desktop, which the agent
> service's own process (Session 0, like every Windows service) never is -
> so it now runs in a tiny one-shot self-invocation of the agent exe
> (`DeployCoreAgent.exe --set-resolution W H`), launched into the active
> session the same way ffmpeg's own capture is, rather than in-process.
> Ctrl+Alt+Del has a toolbar button in both Shadow and Connect.
>
> **Shadow works with nobody logged in, too** - not just once someone's
> signed in. The agent runs in Session 0, which has no access to the real
> console by default; `SessionCapture.cs` launches the actual capture into
> the active console session instead. The FIRST version of this fallback
> (retargeting the service's own SYSTEM token to the target session)
> compiled and ran with no error, but real testing showed it didn't
> actually work - ffmpeg's capture file never appeared. Replaced with the
> mechanism a real, shipping tool (rustdesk/rustdesk) actually uses:
> find explorer.exe (someone's logged in) or winlogon.exe (nobody has -
> it's the process that owns and renders the logon/lock screen, and exists
> in every session regardless of login state) already running in the
> target session, and steal *that* process's own token directly, rather
> than manufacture one. Not yet re-confirmed on real hardware as of this
> write-up - see `remote-agent/agent/SessionCapture.cs`'s own doc comment
> for the full reasoning and citations.
>
> **Connect mode**: a real bug (closing the WebSocket before accepting it,
> which the ASGI spec says produces a bare HTTP 403 instead of a real close
> event) was found and fixed - confirmed live, the browser now gets a real
> connection instead of an opaque refusal. Past that point, a session was
> still seen hanging at "Establishing a secure session" with no error -
> static review of the whole tunnel (browser → backend → agent → target
> RDP port) didn't turn up a further bug, so byte-progress logging was
> added on both ends of that tunnel instead of guessing again; the next
> real test's logs should show conclusively which leg, if any, isn't
> moving bytes.

## Capabilities

### Setup, updating & instance identity
- One-time setup wizard (instance name + first admin account, username and
  password required, email optional); locked out (`409`) once any user
  exists; auto-creates a default global disk layout
- One-command install (`scripts/setup.sh`): generates `APP_SECRET_KEY` if
  left blank, detects this host's own LAN IP and sets `APP_PUBLIC_URL` to it
  if that's still at its shipped default (see "Quickstart" above for why
  that setting matters and when to override the detected value), builds and
  starts everything
- Database migrations run automatically on every `api` container start
  (`backend/entrypoint.sh`), no manual step for first install or later
  updates
- Self-update: Settings → Updates shows current commit and how many commits
  behind `origin` you are (refreshed automatically every 5 minutes by the
  `updater` service), one button pulls, rebuilds, migrates, and restarts,
  with a live staged progress indicator. No configuration needed: the
  updater container figures out this repo's real path on the host itself
  (asking the Docker API for its own bind mount's source), so it works the
  same on a brand new install and on an instance that's been running since
  before this feature existed. Falls back cleanly (a clear "self-update
  unavailable" message) if this isn't a git checkout
- DeployCore ships its own default branding (an icon + favicon, shown in
  the sidebar and sign-in screen); a global admin can replace it with the
  MSP's own name and logo from Settings → MSP Organization at any time.
  Logo accepts PNG, JPEG, or SVG up to 5 MB
- Dashboard shows a "Getting started" checklist (add a hypervisor, upload a
  Windows ISO, create a template) for any organization that hasn't
  completed first-time setup yet, disappears once all three exist

### Security
- Username + password (argon2 hashing), with a minimum password policy
  (8+ characters, at least one letter and one digit) enforced on account
  creation and password changes
- Optional TOTP two-factor authentication per user (Account page): enroll
  by scanning a QR code with any standard authenticator app (a manual
  entry key is also available if you can't scan), login then requires a
  second-step code
- Session revocation: every login is tracked in Redis, not just a stateless
  JWT. "Sign out everywhere" (Account page) or an admin's "force logout"
  action on another user (Users page) immediately invalidates every
  outstanding token for that account, not just the current one
- Login rate-limited to 10 attempts / 5 minutes per source IP and per
  username

### Organizations
- List (scoped to what the caller can see: all orgs for a global-role user,
  else only orgs they have an explicit role in), create, view, edit
  (name/description/active flag)
- Delete (global-admin only): permanently removes the organization and
  everything scoped to it (hypervisors, disk layouts, templates, ISO
  assets and their files on disk, deployments, webhooks, settings, every
  user's role assignment for it), gated behind a confirmation dialog that
  spells out what's about to go. Audit log rows for the organization
  survive with their org reference cleared, so there's still a permanent
  record the deletion happened. Doesn't touch any VM already created on
  the organization's hypervisors, only DeployCore's own record of how to
  reach them

### Users
- Global admin only: list, create (username/password/display name/optional
  email/global role), edit (display name/global role/active flag/password),
  force-logout (revokes every active session for that user immediately),
  reset 2FA (shown only when the user has 2FA enabled - unlike the
  self-service `/auth/2fa/disable`, no code from the user is required, an
  admin who can already reset that user's password is equally trusted to
  clear it. Account-recovery path for someone who's lost their
  authenticator device; doesn't touch active sessions, only future logins)
- Self-service password change (Account page): requires the current
  password, unlike an admin's reset above. Revokes every session for the
  account afterward, same as "Sign out everywhere" - the frontend clears
  local state and redirects to the login page right after, since the
  session that just made the request no longer works either
- Access is managed inline from the Users list: an "Assign..." dropdown
  next to each user's existing roles offers every organization they don't
  already have a role in, plus a "Global (all organizations)" option at
  the top for granting or changing their global role the same way, no
  separate edit form needed just for that. A user's global access (when
  they have one) shows as its own removable "Global: role" badge alongside
  their per-organization ones
- Users are identified and log in by username, not email. Email is optional,
  used only for M365 notification delivery if configured (see below)
- Each user can set their own profile picture (Account page: PNG or JPEG,
  under 2 MB), shown next to their name in the sidebar and in the Users
  list; falls back to their initials if none is set

### Hypervisors
- Per-organization. ESXi only - the only hypervisor driver this project
  targets.
- Fields: name, API endpoint, username, credential (write-only, never
  returned by the API after creation), TLS verification toggle, default
  datastore (used when creating a VM if a template doesn't set its own
  `preferred_datastore`). The network a VM's NIC attaches to is defined
  per-template instead (see Deployment Templates below), not on the
  hypervisor connection
- **List datastores**, next to Default datastore in the create form:
  same "test the credentials you just typed in, nothing saved yet"
  reasoning as Test Connection below, an ad-hoc `POST .../hypervisors/
  list-datastores-adhoc` call using the form's current endpoint/
  username/password to build a throwaway driver and list what it can
  see, filling in the field's own live autocomplete - no need to save
  the host first just to find out what it's called on the datastore side
- Test Connection, two forms: an ad-hoc test against whatever is currently
  typed into the create form, before anything is saved, and a
  test-connection button on an already-saved host that updates and displays
  its last-test status/timestamp/message
- Admin-only for create/edit/delete/test; readonly+ for viewing

### Disk Layouts
- Named, reusable partition schemes, org-scoped or global
- Fields: EFI partition size (MB), MSR partition size (MB), optional
  recovery partition size (MB, placed between the MSR and OS partitions
  using the WinRE/recovery GPT partition type), OS volume (either a fixed
  size in MB or "remaining disk space"), an arbitrary list of additional
  volumes (label, drive letter, size in MB)
- **Setting a recovery partition size is what avoids the well-known
  "recovery partition blocks disk expansion later" problem**: Windows
  Setup's own default behavior (recovery size left unset/`null`) is to
  append its own WinRE recovery partition *after* the OS volume, so a
  later hypervisor-side disk expansion can't extend C: into the new
  space, it's blocked by the recovery partition sitting right after it.
  With a recovery size set, DeployCore's own `<DiskConfiguration>`
  declares the recovery partition *before* the OS volume instead (EFI,
  MSR, Recovery, then OS last) - the OS volume stays the last partition
  on disk and can always be extended, no post-install partition surgery
  ever needed. `scripts/seed.py`'s demo layout sets this (EFI 500 MB, MSR
  128 MB, recovery 1024 MB, OS volume remaining) for exactly this reason
- Optional **post-install scripts** (same `{name, script_text}` shape as a
  template's own, run over WinRM): unlike a template's scripts, these run
  as the very first thing `run_post_install` does, before VMware Tools,
  before any Windows feature or app install - for disk/partition fixups
  that need a pristine, freshly-booted disk before anything else touches
  it. A failure here stops the deployment rather than continuing past it,
  unlike app installs
- Rendered directly into the generated `autounattend.xml`'s
  `<DiskConfiguration>` block
- Create/edit (operator+) for org-scoped layouts, including the
  post-install scripts editor; a separate global-create endpoint exists
  for admins but has no dedicated UI form yet (global layouts can be
  created but not edited/deleted through the UI, same limitation
  templates have)
- Export (readonly+, downloads a JSON file, post-install scripts included)
  and import (operator+, uploads that JSON as a new org-scoped layout),
  and a delete button (operator+)

### ISO Assets
- Org-scoped or global. Windows Server ISOs only, uploaded through the UI
  as `windows_iso`. (A `virtio_iso` kind also exists in the data model,
  for a hypervisor whose driver needs one injected during Setup - ESXi
  doesn't, so nothing in the UI offers uploading one today.)
- A global admin gets an "Available to" scope choice in the upload dialog
  (this organization only, or every organization); a global ISO is
  inherited read-only by every organization the same way global disk
  layouts and templates already are (`POST /api/iso-assets/global` and its
  own chunk/finalize/delete routes, global-admin only)
- Chunked upload from the browser (8 MB chunks over sequential POSTs, then
  a finalize call that assembles the file, computes its SHA-256, patches
  out the boot prompt for a `windows_iso` (see the deployment pipeline
  section below), and marks it `complete`), built for multi-gigabyte ISOs
  without loading the whole file into memory client- or server-side
- For a `windows_iso`, finalize also detects every Windows edition bundled
  in its `install.wim` (`app/services/windows_edition_detect.py`): Windows
  Server media typically ships several editions (Standard/Datacenter x
  Server Core/Desktop Experience) in one file, selected only by a numeric
  `/IMAGE/INDEX` in the answer file. Detection lists and extracts the WIM
  with `7z` (`p7zip-full`), not `xorriso`: Microsoft's own ISO builder lays
  Windows Setup media out as a UDF+ISO9660+Joliet hybrid specifically so
  `install.wim` can exceed the 4&nbsp;GiB plain-ISO9660 limit, which a
  multi-edition Server WIM routinely does, and `xorriso` 1.5.4 (Debian
  bookworm) silently truncates such a file on extraction (confirmed against
  a real >4&nbsp;GiB UDF test image: exit 0, "files restored", extracted
  file a fraction of the real size, no error raised at all) even though it
  can list it just fine; `7z` reads the UDF tree directly and extracts the
  full, correct byte count in the same scenario. Extraction is read-only
  against the ISO either way, this is unrelated to the in-place boot-prompt
  patch above, which still uses `xorriso` (a tiny UEFI boot image, nowhere
  near the size where this matters). Detection then reads the extracted
  WIM's embedded metadata with `wimlib-imagex info --extract-xml` (UTF-16
  XML), storing the result as `iso_assets.windows_editions` (JSONB list of
  `{index, name, description, has_gui}`). `has_gui` is derived from the
  WIM's own `FLAGS` value (every Core, no-GUI SKU's flag ends in `Core`,
  e.g. `ServerStandardCore`; every Desktop Experience one doesn't), not
  guessed from `name`/`description` text, since those aren't guaranteed to
  spell out "(Desktop Experience)" on every ISO (older/localized media);
  it's stored for future use but the template form's dropdown currently
  just shows each edition's own `description` (falling back to `name`),
  e.g. "Windows Server 2025 Standard (Desktop Experience)", trusting that
  Microsoft's own wording is self-explanatory rather than layering an
  index number and a GUI/No GUI tag on top of it.
  Best-effort: any failure (non-Microsoft media, no install.wim/.esd, tool
  failure) just leaves it `[]`, it never
  blocks the upload. Templates use this list to offer a named edition
  dropdown instead of a bare index number, see Deployment Templates below
- Delete removes the database row and the file on disk. A template that
  references the ISO doesn't block the delete: `deployment_templates
  .iso_asset_id` is `ON DELETE SET NULL` (migration 0021), so the delete
  commits first and the referencing template just has its ISO cleared and
  can't deploy until a new one is attached, exactly what it says on the
  confirm dialog. The delete route also unlinks the file from disk only
  after that commit succeeds, not before, so a delete that fails for any
  reason never leaves an orphaned file with a database row still pointing
  at it

### App Assets
- Org-scoped or global, same visibility/inheritance model and the same
  chunked-upload flow as ISO Assets (`POST /api/app-assets/global` and its
  own chunk/finalize/delete routes for global ones, admin-only). Scope is
  fixed at upload time (choose "This organization only" or "Every
  organization (global)"), not changeable afterward - same as templates
  and ISOs, nothing in this codebase supports migrating an asset between
  org-scoped and global after creation
- Editable after upload (`PATCH .../app-assets/{id}`, org-scoped and
  global variants, operator+/admin same as everything else here): name,
  kind, and default silent-install arguments can all be changed without
  re-uploading. The uploaded file itself is immutable - a mistaken kind
  or a vendor changing their silent-install flags doesn't require
  deleting and re-uploading the asset (which would also mean re-editing
  every template that references it), just editing the metadata; a
  genuinely new file (a version bump) is still a new asset, re-upload as
  one and repoint templates at it
- An MSI or EXE installer (`kind`), a display name (independent of the
  uploaded filename, e.g. "Datto RMM Agent" vs. `AgentSetup_1.2.3.exe`),
  and default silent-install arguments (e.g. `/qn /norestart` for an MSI,
  or whatever an EXE's own convention is, commonly `/S`/`/silent`/
  `/verysilent`/`/quiet`); a template can override the arguments per
  attachment without touching the asset itself. Multiple flags in one
  field work fine, including quoted values with spaces (e.g.
  `/S /v"/qn REBOOT=ReallySuppress"`): `WinRMClient.install_app` passes
  `install_args` through as a single raw command-line string, never split
  into a PowerShell array, specifically so a multi-flag string survives
  intact instead of becoming one glued-together quoted argument
- Attached to a template's `app_installs` (ordered list of
  `{app_asset_id, install_args}`), installed over WinRM during
  post_install, after Windows features and RDP and before post-install
  scripts and the final reboot - so a post-install script can assume an
  app installed earlier in the list is already present, and an app that
  reports needing a reboot to finish actually gets one (briefly moved
  to run after the final reboot instead, on the theory that a settled,
  fully-patched guest is a more representative install target - reverted,
  since that meant an app's own requested reboot never happened at all).
  Not a foreign key: `app_installs` is a JSONB column, a deleted app
  asset is skipped with an error log line at deploy time rather than
  blocking the delete or failing the whole deployment
- Delivery is guest-initiated, not worker-pushed: the guest's own
  `Invoke-WebRequest` downloads the installer directly from DeployCore's
  API (`GET /api/deployments/{id}/app-assets/{app_id}/download`), the
  same reasoning as the Setup-complete callback, the guest already
  reaches DeployCore, so there's no need to chunk the file through WinRM.
  That endpoint is authenticated by a random per-deployment token
  (`deployments.app_asset_access_token`), generated right before the
  first app install and cleared right after (or on failure), not a user
  session, there isn't one to authenticate the guest with
- Each install actually runs inside a one-shot SYSTEM-context Scheduled
  Task on the guest (`WinRMClient._run_as_system_task`, shared with
  Windows Update), not directly over the WinRM session driving it - a
  network-logon WinRM token is refused by some COM APIs (confirmed for
  Windows Update) and several installer frameworks (NSIS, which
  Firefox's own installer uses, among them) have documented bugs picking
  a per-machine-vs-per-user install mode correctly in silent mode
  specifically; running as SYSTEM sidesteps both, and empirically biases
  well-behaved installers toward a machine-wide install the same way
  `winget --scope machine` does, since there's no ambiguous "current
  interactive user" profile to install into instead. Task, its script
  file, and its result file are all removed afterward regardless of how
  the install ends - success, a timeout, or a WinRM hiccup mid-poll
  (confirmed a real, not just theoretical, way to leave one behind
  otherwise) - via `try`/`finally`, with `Stop-ScheduledTask` alongside
  `Unregister-ScheduledTask` so a genuinely stuck task actually gets
  killed rather than just deregistered while still running
- MSI installs run through `msiexec /i "<path>" <args>`, with `ALLUSERS=1`
  forced automatically unless `install_args` already sets it (the one
  universal MSI property for a machine-wide install); EXE installs run
  the downloaded file directly with `<args>` passed straight through -
  there's no equivalent single flag that works across every EXE
  installer framework, that still has to live in that asset's own
  `install_args`. Exit code 3010 (success, reboot required) counts as
  success alongside 0 for both
- Verified, not just trusted to have exited cleanly: `-Wait -PassThru`
  only waits for the process directly launched, and a self-relaunching
  or detached-child installer can report a clean exit code without the
  app actually being there yet (confirmed on a real deployment). Each
  install snapshots the registry Uninstall key set (HKLM + WOW6432Node,
  plus HKCU as cheap defense in depth) before running, then polls for a
  new entry to appear afterward for up to 10 minutes - the same
  approach Chocolatey (`Get-UninstallRegistryKey`) and the PowerShell
  App Deployment Toolkit both use, since there's no universal "did this
  arbitrary installer actually finish" API
- **Prefer an MSI package over an EXE installer whenever the vendor
  offers one.** Confirmed the hard way with Firefox: a generic vendor
  "download" link/button can silently hand you the small stub installer
  (a bootstrapper that fetches the real payload over the network *during*
  the silent install itself) rather than the self-contained full/offline
  installer, with no obvious naming difference and no error at all if
  that runtime fetch fails quietly under whatever network/proxy context
  the install actually runs in - it exits 0 having installed nothing, and
  looks identical in the deployment log to a genuinely finished install
  right up until the registry-diff verification above (correctly) never
  sees a new entry appear. Mozilla's own enterprise/automation guidance
  is to use their official MSI instead of either EXE variant for exactly
  this reason. MSI sidesteps it structurally: it's always a single
  self-contained package (no stub/full ambiguity to accidentally pick the
  wrong one of), `ALLUSERS=1` is forced automatically for a machine-wide
  install without needing to know a given vendor's own EXE conventions,
  and `msiexec`'s exit codes are standardized rather than
  installer-framework-specific. If an app only ships as EXE, prefer the
  vendor's explicitly-labeled "full"/"offline" installer over whatever a
  plain "Download" button on their homepage serves by default

### Deployment Templates
- Org-scoped or global (global templates are inherited read-only by every
  org and can be cloned into an org-scoped copy)
- Fields: name, Windows ISO (nullable, a template can exist before an ISO
  is attached; the pipeline refuses to deploy from it until one is set),
  `image_index` (which edition inside that ISO's `install.wim` to install,
  rendered into the answer file's `/IMAGE/INDEX`; defaults to `1`, which is
  not a considered choice, it's whatever was hardcoded before this field
  existed and is typically Server Core, no GUI, on Microsoft's standard
  multi-edition ordering. The UI shows a dropdown of the ISO's own detected
  `windows_editions` when available, each option labeled with its actual
  edition name (e.g. "Windows Server 2025 Standard (Desktop Experience)")
  instead of a bare number, a plain number field otherwise),
  disk layout, CPU count and cores per socket, RAM (edited in GB - up to
  two decimal places, e.g. 1.5 - converted to whole MB, what the
  API/ESXi actually use, right before submit), disk size (GB) and
  disk provisioning type (thin / thick lazily zeroed / thick eagerly
  zeroed), network name (an ESXi/vCenter port group, network segmentation
  is expected to already be handled by picking the right port group, not
  by a VLAN tag on the template) and network adapter type (VMXNET3, E1000,
  or E1000E). Network name and preferred datastore (below) are both plain
  text fields with a live autocomplete list: pick a hypervisor from the
  small "Browse..." dropdown next to either one and `HypervisorDriver.
  list_networks()`/`list_datastores()` fill in the actual port groups or
  datastores that host can see - the stored value is always just the
  name string either way, not a binding to whichever host you happened
  to browse from. Also locale/timezone/keyboard layout as Windows
  identifiers not IETF/IANA ones (new templates default to `de-DE`/
  `W. Europe Standard Time`/`de-CH`), a local administrator password
  (write-only), and an off-by-default **custom admin account** toggle:
  off (default) uses the built-in Administrator account as-is with that
  password; on adds a username field (default `svcadmin`, can't be
  `Administrator`/`Guest`/`DefaultAccount`/`WDAGUtilityAccount`, all
  reserved) and creates that as a genuinely new local account instead,
  disabling the built-in Administrator during Setup (see the deployment
  pipeline section below). The API enforces this regardless of what a
  caller sends: `local_admin_username` is always forced back to
  `"Administrator"` server-side whenever the toggle is off, optional
  domain join (FQDN, join account, join
  credential [write-only], target OU, and timing, `answer_file` bakes the
  join into the unattended install, `post_install` joins afterward over
  WinRM),
  Windows roles/features picked as checkboxes from a curated list scoped to
  what a standalone Windows Server actually needs (AD Domain Services, DNS,
  DHCP, Web Server (IIS), Print Services, Remote Desktop Session Host, DFS
  Namespaces, DFS Replication), an ordered list of app installs (App Assets
  to install, with optional per-attachment argument overrides, run after
  roles and before post-install scripts, see the App Assets section above),
  and a list of post-install PowerShell scripts (name + script text, run in
  order after both), an on-by-default **install Windows updates**
  toggle (see the Deployments pipeline section below for what that step
  actually does and its size-skip trade-off) - off for deployments where
  speed matters more than shipping fully patched - and an on-by-default
  **install VMware Tools** toggle (see the same section for
  `mount_tools_installer`/`install_vmware_tools`/eject-after) - off for a
  non-ESXi host in the future, a host without the Tools ISO in its own
  depot, or simply a deployment that doesn't need `get_guest_ip()` (e.g.
  a DHCP deployment reachable some other way already). Each role is installed
  with a plain `Install-WindowsFeature`, nothing more: AD Domain Services
  installs the role binaries only, not a forest/domain promotion or any
  GPO/OU/delegation setup, do that yourself afterward (`Install-ADDSForest`
  or `dcpromo`, then GPMC) if you need a real domain controller
- Create/edit/delete (operator+); editing a password/credential field blank
  leaves the stored value unchanged. Editing a template only affects
  deployments created from it afterward, deployments already completed or
  in progress are unaffected (shown as a note directly in the edit form)
- Delete: `deployments.template_id` is `ON DELETE SET NULL` (migration
  0028, the same pattern as `deployment_templates.iso_asset_id` above),
  so a template with existing deployments deletes cleanly, it never
  blocks on those deployments the way a plain foreign key would. A
  deployment whose template gets deleted while it's still actively
  provisioning (pending, or retried after the delete) fails with an
  explicit "template ... has since been deleted" error instead of
  crashing, everything already completed or failed keeps its full log
  history regardless
- Clone: duplicates any visible template (own org's or an inherited global
  one) into a new org-scoped copy named "<name> (copy)", including
  encrypted credentials (copied as ciphertext, not re-entered)
- Export (operator+, JSON file, secrets excluded but the local admin
  *username* travels since it isn't one, disk layout inlined, ISO recorded
  only as an informational filename/kind hint) and import (operator+,
  recreates the disk layout as a new row, leaves the ISO unattached, sets a
  random placeholder local administrator password that must be replaced
  before the template can deploy)
- Preview: renders the exact `autounattend.xml` that would be built for a
  given hostname/network configuration, without creating a deployment,
  byte-identical to what actually ships

### Deployments
State machine (enforced server-side, illegal transitions rejected):

```
pending → creating_vm → booting → installing_os → post_install → configuring → completed
                                                                              ↘
                                                                  failed (from any non-terminal state)
```

- Wizard: select template → select hypervisor → hostname + IP config (DHCP
  or static: address/netmask/gateway/DNS) → autounattend.xml preview →
  deploy. A "Bulk deployment" toggle replaces the single hostname field
  with a prefix and a count (1-50): creates that many deployments in one
  go, hostnamed `PREFIX01`, `PREFIX02`, etc. DHCP only, bulk doesn't attempt
  per-VM static IP allocation
- **Customize installation** (single-deployment only, not bulk): once a
  template is picked, a button opens the exact same fields as the
  template's own edit modal (`TemplateFieldsForm`, shared by both), all
  pre-filled from that template, editable for just this one deployment
  without touching the template itself. Stored as `Deployment.
  overrides_encrypted` (encrypted at rest, same as template secrets - an
  override can include a plaintext admin password), applied via
  `EffectiveTemplate` (`services/template_effective.py`), a thin
  `__getattr__` wrapper layering the overrides dict over the real
  `DeploymentTemplate` object: every existing `template.*` read
  throughout `provision.py`/`template_render.py` works completely
  unchanged whether a given deployment has overrides or not, no
  per-field plumbing needed at any of those call sites. Leaving a
  password/credential field blank in the modal is stripped before
  submission rather than sent as an override (would otherwise overwrite
  the template's real secret with an empty string) - same "blank means
  unchanged" convention template editing already has, just enforced
  client-side here since there's no PATCH endpoint's own logic to lean on
- **Preferred datastore** is a real `DeploymentTemplate` field
  (`preferred_datastore`, nullable - just a name, not a foreign key to a
  specific `HypervisorHost`, the same way `network_name` isn't bound to
  one either), editable anywhere template fields are editable (template
  create/edit, and "Customize installation" above) rather than being a
  one-off wizard-only setting: same `EffectiveTemplate` mechanism as
  every other field, no special-casing needed in `provision.py` beyond
  `template.preferred_datastore or host.default_datastore` when building
  `VmSpec`. The field itself is a text input with a live autocomplete
  list (`<datalist>`) populated on demand: pick a hypervisor from a
  small "Browse datastores from..." dropdown next to it and
  `HypervisorDriver.list_datastores()` (ESXi: `ComputeResource.
  datastore`) fills in the options, but the stored value is always just
  a plain string, so it still means something even if the deployment
  ultimately lands on a different host than the one you browsed from
- **Hostname is capped at 15 characters** (13 for a bulk prefix, the 2-digit
  suffix takes the rest), enforced both client-side and by the API
  (`schemas/deployment.py`). This is a hard Windows constraint, not a
  DeployCore choice: `ComputerName` in the specialize pass is a NetBIOS
  name, and Windows Setup doesn't truncate a longer value the way some
  other Windows-name fields do, it fails to process the whole answer file
  partway through installation instead, well after the VM's already been
  created and Setup's copied every file. That failure shows up as a
  generic "the computer was unexpectedly restarted, Windows installation
  cannot continue" dialog on the console with nothing in DeployCore's own
  log stream (it happens before OOBE, before the guest has ever reached
  a state where it could call back), which is exactly what makes it worth
  blocking at submission instead of discovering the hard way
- Pipeline (runs in the background worker, not the request thread): renders
  the answer file, builds a per-deployment answer-file floppy image,
  uploads the Windows ISO and the answer-file floppy to the hypervisor
  datastore, creates the VM (UEFI firmware, LSI Logic SAS controller on
  ESXi, not VMware's own PVSCSI: Windows has no inbox driver for PVSCSI,
  a boot disk on it can't be recognized during Setup or on later boots
  without injecting the VMware Tools PVSCSI driver first, which nothing
  here does; LSI Logic SAS needs no driver injection on any Windows
  Server version, at the cost of PVSCSI's performance edge on very
  high-IOPS workloads; a USB 3.0/xHCI controller too, best-effort and
  separate from VM creation itself so a failure here can never block a
  deployment: ESXi presents an absolute-positioning USB tablet over it
  automatically, no VMware Tools required, without it a fresh guest only
  has the default PS/2 mouse, which the ESXi/vSphere web console can't
  track properly, the cursor doesn't reliably show up or move with the
  actual pointer at all, confirmed against Packer's own vsphere-iso
  builder source, which constructs exactly `VirtualUSBXHCIController`
  with no other properties for its own "xhci" option, specifically
  documented there as "needed for mouse during install without VMware
  Tools"),
  attaches media (a floppy, not a second CD-ROM: a second CD-ROM does get
  found and applied for most of the answer file, but empirically not
  reliably for Setup's very first implicit check, the one deciding
  whether to show the interactive language/time/keyboard screen, which
  runs before its driver stack is fully up; a floppy is both checked
  earlier and higher-precedence there per Microsoft's own documented
  search order), sets the boot order to disk before CD-ROM, not the other
  way around: on the very first boot the disk is blank and fails,
  falling through to the CD-ROM within the same boot pass (ESXi's
  `bootRetryEnabled`/`bootDelay` handle that fallthrough and the case
  where a freshly attached CD-ROM isn't fully connected the instant the
  VM powers on), but once Windows Setup has written a bootloader to the
  disk partway through installation, every later boot (including the one
  Setup itself triggers mid-install to continue on the disk) goes
  straight to it instead of landing back in the CD's WinPE, which is
  what CD-ROM-first did and is why it threw "the computer was
  unexpectedly restarted" on the first post-file-copy reboot, a
  documented Packer/vSphere-iso gotcha, not anything specific to this
  setup, then powers on
- A Windows install ISO is patched once, the moment it finishes uploading
  (not per deployment), to remove Setup's own "Press any key to boot from
  CD or DVD..." prompt: every stock ISO ships a second, silent UEFI boot
  image (`efisys_noprompt.bin`) alongside the normal one, built by
  Microsoft for exactly this kind of unattended deployment; `iso_remaster.py`
  finds it and swaps it into the boot catalog's actual slot via `xorriso`,
  rewriting only that one boot image. Deployments still send a tight round
  of synthetic Enter keypresses right after power-on (15s, one per second)
  as a safety net for ISOs where the swap wasn't possible (media without
  that second boot image), plus a second, sparser round over the next 45
  seconds (60s total from power-on) for Windows Setup's own
  windowsPE-stage language/time/keyboard screen, which
  has a long-standing upstream quirk on some locales where `InputLocale`
  isn't honored on that one specific screen even though the rest of the
  answer file (including the specialize-pass locale, see below) applies
  correctly. `booting → installing_os` is driven by the worker itself, right
  after power-on and the synthetic-keypress rounds above, not by the
  callback: there's no way to observe real progress inside WinPE/Setup at
  all, so `installing_os` is deliberately the state for that entire
  black-box window, not just its very end. The guest's `FirstLogonCommands`
  always: enable WinRM and open a firewall rule for it; call back to
  `/api/callback/{token}` (single-use per-deployment token, `secrets.token_hex(8)`,
  lowercase hex rather than `token_urlsafe`: this is one of the few tokens
  in this codebase that realistically ends up read off a screen and typed
  in by hand, debugging a stuck deployment on a hypervisor console with no
  clipboard access being exactly when that happens; hex has no
  case-sensitivity to get wrong and none of base64url's visually
  ambiguous pairs like `0`/`O` or `1`/`l`/`I`, still 64 bits, far beyond
  brute-force reach for a single-use token only ever valid during one
  install window - sets `callback_token_used`, which `wait_for_callback`
  polls for instead of a state change, since the state's already
  `installing_os` by the time this fires). `wait_for_callback` doesn't
  depend solely on that one outbound POST landing: every
  `FALLBACK_REACHABILITY_POLL_EVERY`th poll (roughly every two minutes,
  not every 15-second poll, no reason to hit the hypervisor API and
  attempt a guest connection that often while Setup is still in its much
  longer WinPE/specialize/OOBE phase, before `FirstLogonCommands` can even
  run) it also checks whether the guest has become reachable over WinRM -
  the same `FirstLogonCommands` batch enables WinRM before it sends the
  callback, so a guest answering over WinRM is equally good evidence
  Setup finished, even if the callback itself never got through (seen in
  practice: a firewall/network-segmentation gap between an isolated
  deployment VLAN and DeployCore's own network blocking just that one
  outbound call while everything else, including the static IP itself,
  worked correctly). A static deployment's IP is used directly for this
  check rather than asking the hypervisor for it: that query
  (`get_guest_ip`) depends on VMware Tools being installed in the guest to
  report anything at all, and a static deployment already knows its own
  IP declaratively, no need to ask. If that fallback fires, a log entry
  says so explicitly rather than looking identical to a normal callback.
  The reachability check itself (`_winrm_reachable`, shared with
  `run_post_install`'s own WinRM-wait loops below) always runs through
  `asyncio.to_thread` with a hard `WINRM_CHECK_TIMEOUT_SECONDS` ceiling,
  not a bare call: pywinrm has documented gaps in its own timeout
  handling and can hang well past whatever it's configured with against
  a host with nothing listening yet, the normal state for most of any of
  these loops' lifetime - a real bug caught during testing, where a
  single hanging check silently stalled `wait_for_callback` entirely
  (never reacting to a callback that had, in fact, already landed) rather
  than just failing that one attempt and moving on.

  **No human ever needs to log in at the console**, but not via
  auto-logon: two different attempts at that (the declarative `AutoLogon`
  element, and a specialize-pass `RunSynchronousCommand` writing the same
  `HKLM\...\Winlogon` registry values via `reg.exe`) each independently
  broke Setup outright with the identical `WINDEPLOY 0x80220005` failure -
  different mechanisms, same registry surface, same result, both fully
  reverted, permanently. That pattern pointed at the Winlogon auto-logon
  state itself being what this Windows Server build's OOBE launch chokes
  on here, not "any specialize-pass automation" in general - role
  installs, static IP, and everything else in this pipeline all work fine
  without ever touching Winlogon. So instead: `_specialize_enable_winrm.xml.j2`
  gets WinRM listening during the specialize pass itself, well before
  oobeSystem, via `winrm.cmd quickconfig -quiet` (a WSH/VBScript wrapper,
  deliberately not PowerShell's `Enable-PSRemoting` - the one specialize-pass
  attempt that used PowerShell here crashed Setup a third, different way,
  "the computer was unexpectedly restarted", and there's no way to be
  certain in advance that was specifically the WMI/CIM-readiness issue
  documented elsewhere rather than something more general about
  PowerShell's own startup this early) plus a `netsh.exe` firewall rule.
  WinRM is a service, not an interactive session, so this is enough:
  `wait_for_callback`'s WinRM-reachability check (above) now normally
  fires well before Setup even reaches oobeSystem, carrying the rest of
  the pipeline the same way a landed callback always did.
  `FirstLogonCommands` still exists as defense-in-depth for the rare case
  a human does end up at the console (troubleshooting, or if that
  specialize-pass step somehow didn't take for a given deployment) -
  redundant in the common case, harmless either way.

  The WinRM-reachability fallback above isn't a complete answer on its
  own, though: for a **DHCP** deployment specifically, it depends on
  `HypervisorDriver.get_guest_ip()`, which needs VMware Tools (or
  hypervisor-level DHCP snooping that isn't guaranteed on every host or
  ESXi version) to report anything - and Tools isn't installed until
  *after* `installing_os` finishes, a chicken-and-egg gap that left a
  real deployment sitting in `installing_os` indefinitely despite
  Windows Setup having genuinely completed (confirmed directly on the
  console), because neither the callback (needs a login that never
  happens) nor the fallback (needs an IP that was never reported) ever
  fired.

  Fixed with a dedicated **network ping**, not by reusing the main
  callback: `_specialize_network_ping.xml.j2` POSTs to
  `/api/callback/{token}/network-ping` unconditionally during the
  specialize pass, via `curl.exe` (an inbox tool on Server 2019+, same
  reasoning as `winrm.cmd` over `Enable-PSRemoting` - native, not
  PowerShell, avoiding that same specialize-pass crash risk). That route
  only ever records `guest_reported_ip` - it deliberately does **not**
  set `callback_token_used`. An earlier version hit the main callback
  route directly instead, which broke the one invariant
  `wait_for_callback`'s whole poll loop depends on: that
  `callback_token_used` being true means Setup is actually, fully done.
  It used to only ever get set from `FirstLogonCommands`, which only
  runs after Setup completely finishes (OOBE included, past any of
  Setup's own later internal reboots) - specialize runs minutes before
  that, so the earlier version let `wait_for_callback` see
  `callback_token_used` flip true while Setup was still actively
  mid-install, immediately ejecting the install media (which Setup might
  still have needed) and handing off to `run_post_install`, which then
  polled WinRM against a guest that hadn't actually finished installing
  yet - confirmed on a real deployment that looked stuck with nothing
  network-related to explain it. The WinRM-reachability fallback above
  now checks `guest_reported_ip` before falling back to
  `get_guest_ip()`, so a DHCP guest's address is available from this
  ping well before Tools would ever report one - but that fallback still
  only ever proceeds once WinRM is genuinely, repeatedly reachable,
  exactly the same completion guarantee a landed callback always
  provided, so nothing here shortcuts it.

  A single bare `curl.exe` call wasn't actually enough either, confirmed
  live: curl's own `--retry` flag only covers a specific set of
  transient errors (timeouts, HTTP 408/429/5xx) - **not** "connection
  refused" or "network unreachable", which is exactly what a DHCP guest
  gets if the specialize pass reaches this command before DHCP has
  actually finished negotiating a lease, a real race that isn't
  guaranteed to lose. `--retry` alone was silently useless against it.
  The actual fix wraps the whole `curl.exe` call in a `cmd.exe /c "for
  /l %i in (1,1,24) do (curl.exe ... && exit /b 0 || ping -n 6
  127.0.0.1 >NUL)"` loop instead - up to 24 attempts, a delay between
  each - so the retry happens at the "does this machine have a working
  route yet at all" level, not just curl's own narrower definition of
  transient. `ping -n 6 127.0.0.1` as the delay, not `timeout.exe`:
  `timeout` refuses to run without a real attached console ("ERROR:
  Input redirection is not supported"), which a specialize-pass command
  never has - pinging loopback a fixed number of times is the
  standard console-independent way to get a few seconds of delay in a
  Windows batch context.

  VMware Tools installs post-install, over WinRM, as the very first step
  of `run_post_install` - before the static-IP cross-check, before any
  Windows feature or app install, before the final reboot
  (`WinRMClient.install_vmware_tools`). It used to run during the
  specialize pass instead (`_specialize_install_vmware_tools.xml.j2`, a
  `RunSynchronousCommand` pair), but that crashed Setup outright on a
  real deployment ("the computer was unexpectedly restarted"); a WinRM
  call after Setup has already finished and the OS is fully up carries
  none of that risk, so that file is gone and the same install now runs
  through the same channel already proven for `Install-WindowsFeature`.
  Right before that, `HypervisorDriver.mount_tools_installer` (ESXi:
  `vm.MountToolsInstaller()`) mounts the Tools ISO, itself called from
  post-install rather than at VM creation. That call requires an
  *existing* CD/DVD device to attach the Tools ISO to - confirmed
  against Broadcom's own KB (vix error 21002, "This virtual machine does
  not have a CD-ROM drive configured") after an earlier version, calling
  it at VM-creation time (before the Windows/VirtIO ISOs' own CD-ROM
  devices existed - those only get attached later in the pipeline, once
  uploaded), silently failed on every deployment, swallowed by a bare
  `except Exception: logger.exception(...)`. Rather than adding a
  dedicated CD-ROM device that would then sit on the VM permanently
  (used for a few minutes during Tools install, dead weight for the
  rest of the VM's life), it simply runs late enough to reuse a device
  that already exists and is already empty: the Windows ISO's own
  CD-ROM device, ejected (not removed) by the Setup-complete callback
  handler well before post-install's VMware Tools step ever runs.
  `vm.MountToolsInstaller()` itself is called directly, not wrapped in
  `WaitForTask()`: confirmed live (`'NoneType' object has no attribute
  '_stub'`) and against pyVmomi's own docs that, unlike most vSphere
  operations, this call is synchronous and returns `None`, not a `Task`
  to wait on - `WaitForTask(None)` failed on the client side, which
  meant the exception handler around it treated the whole mount as
  failed and never learned which unit to eject later, even though the
  mount itself had already succeeded on the ESXi side by then (confirmed
  live too: `install_vmware_tools` found the actually-mounted installer
  and ran it successfully regardless, just with the eject-after-use step
  silently skipped since no unit number was ever recorded).
  `mount_tools_installer` identifies exactly which CD-ROM device the
  mount landed on (by its backing, not by assuming a fixed unit -
  nothing in the vSphere API contract guarantees which of the VM's
  existing devices it picks) and returns that unit number; once
  `install_vmware_tools` (and, if it actually installed something, the
  reboot right after) finishes, that same unit gets ejected again
  (`detach_iso`) so the VM ends up in the same clean, driveless-looking
  state regardless of whether this run needed Tools media at all -
  logged either way, not just assumed to have worked. `install_vmware_tools`
  itself then scans for `setup64.exe` across `D:`/`E:`/`F:`, since
  Windows Setup's own install media usually claims `D:`, so Tools
  typically lands on `E:` or `F:` once the guest is up, and
  runs it with `REBOOT=ReallySuppress` - a real, documented VMXNET3
  interaction means the network driver update needs an actual restart to
  take effect cleanly, or the guest loses network access immediately
  ("RPC service unavailable"). `run_post_install` restarts right after,
  but only when something was actually installed; if the ISO was never
  mounted (a non-ESXi host, say), it logs that and moves on - always
  best-effort, never worth failing a deployment over.

  That restart is a full **shutdown + device removal + power-on**
  through the hypervisor (`_shutdown_remove_media_and_power_on`), not
  a guest-initiated `shutdown.exe /r` like every other reboot in this
  pipeline: since a restart is already happening here regardless, it's
  also the one point the answer-file floppy device *and* the CD-ROM
  device `mount_tools_installer` used can actually be **removed**
  rather than just ejected - `HypervisorDriver.remove_floppy`/
  `remove_cdrom` only work while the VM is genuinely powered off
  (`InvalidPowerState` otherwise, the same constraint `detach_floppy`/
  `detach_iso`'s own eject-not-remove approach exists for elsewhere),
  and this is the only reboot in the pipeline that's a real power cycle
  rather than a restart from inside the guest. Confirmed live: the
  Tools CD-ROM device used to only ever get ejected (`detach_iso`,
  logged as "VMware Tools installer media ejected") *after* this reboot
  already completed and the VM was back on - by definition too late for
  `remove_cdrom` to ever apply, leaving an empty-but-present CD-ROM
  device on every completed deployment indefinitely. Moved inside this
  same power-off window instead. `WinRMClient.shutdown()`
  (`shutdown.exe /s`, not `/r`) triggers it; the worker then polls the
  hypervisor's own power state until it actually reports `poweredOff`
  (bounded, `SHUTDOWN_MAX_ATTEMPTS` - if it never gets there, device
  removal is skipped and the VM is powered back on regardless, rather
  than left off indefinitely), removes the floppy and CD-ROM device(s),
  then powers back on and waits for the guest the same way every other
  reboot does (`_wait_for_guest_settled`, the shared tail both this and
  the plain guest-initiated reboots use). Device removal itself is
  best-effort per device: each one was already harmless (ejected,
  empty) either way, a failure here is logged and moved past rather
  than failing an otherwise fully-successful deployment - if this
  reboot never actually reaches `poweredOff` in time, `run_post_install`
  falls back to its older eject-only cleanup for the Tools CD-ROM
  specifically, so it's at least emptied even when it can't be removed
  outright. This is what makes a **DHCP** deployment's guest IP
  discoverable at all: `HypervisorDriver.get_guest_ip()` has nothing to
  report without Tools present, and a real deployment previously spun for
  the full `WINRM_REACHABILITY_MAX_ATTEMPTS` on exactly that gap (see the
  IP-resolution note below - that gap is about the *earlier* step that
  finds the guest's address in the first place, which still can't rely on
  Tools, since Tools isn't installed until after it runs). For a
  **static** deployment, none of this is needed for IP discovery
  (`deployment.static_ip` is already used directly), but Tools gets
  installed anyway, and the static-IP cross-check right after it runs
  benefits from Tools now being up: if the guest-reported IP doesn't
  match the configured static address, a `WARN`-level log line says so -
  not a hard failure, a second NIC or Tools not having reported yet
  aren't actually wrong, but worth an operator's attention if it happens.

  If `template.custom_admin_enabled` is on (off by default), two
  more commands render: `LocalAccountTokenFilterPolicy=1`
  right after enabling WinRM (by default Windows' UAC remote restriction only
  exempts the actual built-in Administrator (RID 500) from a filtered, non-elevated
  token on network logons, without this every WinRM command DeployCore
  runs post-install would silently fail for the template's custom admin
  account even though it's in Administrators), and disabling the built-in
  Administrator account, deliberately last, after the callback, so a
  guest-side quirk from disabling the very account `FirstLogonCommands` is
  running as can never prevent that callback from firing. The custom local
  admin account itself (`template.local_admin_username`) is created
  earlier, declaratively, via a `LocalAccounts` entry in the same
  oobeSystem `UserAccounts` block that always sets the built-in account's
  password too (with the toggle off, that's the only account that gets
  created at all, `local_admin_username` is just `"Administrator"`). The
  callback landing is also the first point DeployCore can be sure Setup is
  done with the install media for good (post-install runs entirely over
  WinRM from here on): `wait_for_callback` ejects the Windows/VirtIO ISOs
  and the floppy alike (drives kept, emptied - ESXi rejects actually
  removing a floppy device while the VM is still powered on, which this
  always runs while it is), and deletes the per-deployment answer-file
  floppy image from the datastore, all best-effort, never worth failing
  an otherwise-successful deployment over. The floppy *device* itself
  does get fully removed later, not just left ejected forever - see the
  VMware Tools reboot below, the one point in the pipeline the VM is
  genuinely powered off
- Locale/keyboard are set in two places in the answer file:
  `Microsoft-Windows-International-Core-WinPE` (windowsPE pass, Setup's own
  UI only) and `Microsoft-Windows-International-Core` (specialize pass, the
  actually-installed OS). Keyboard layout renders as an explicit
  `LCID:KLID` hex pair (e.g. `0807:00000807` for German (Switzerland))
  rather than a bare locale tag, since a bare tag only picks *a* default
  keyboard for that locale, not necessarily its own named one;
  `template_render.py` resolves this automatically for the locales it
  knows about, or passes through an already-hex value unchanged
- A static deployment's IP/netmask/gateway/DNS are set declaratively in the
  answer file's specialize pass (`Microsoft-Windows-TCPIP`/
  `Microsoft-Windows-DNS-Client`, see `_static_network.xml.j2`), live before
  Windows Setup even finishes, not reconfigured over WinRM afterward: that
  used to mean connecting at whatever address DHCP handed out first, then
  reassigning it to the static address remotely, which simply can't work on
  a network with no DHCP server to hand out that first address at all.
  `Identifier` targets the adapter by its MAC address, not by name: a real
  deployment ended up with this component silently never applied at all
  (Setup didn't error, the adapter just stayed on its DHCP default), which
  turned out to match Microsoft's own documented caveat that interface
  alias/LUID matching "is not guaranteed to be the same between different
  builds." `hypervisors/defaults.py`'s `generate_mac_address` assigns a
  MAC explicitly to the VM's NIC at creation time (rather than letting the
  hypervisor generate one), and that same value goes into `Identifier`
  here - deterministic, no enumeration-order guesswork left. Element
  order inside `_static_network.xml.j2` is load-bearing, not stylistic:
  Microsoft's own schema requires `Interface`'s children in the exact
  order `Ipv4Settings`, `Identifier`, `UnicastIpAddresses`, `Routes`
  (confirmed against learn.microsoft.com's own component reference,
  which explicitly warns about this), and `Route`'s children in the
  exact order `Identifier`, `Metric`, `NextHopAddress`, `Prefix`.
  Getting either wrong doesn't raise a helpful XML error, Setup just
  fails specialize-pass processing with a generic "Windows installieren:
  Die Installation konnte nicht abgeschlossen werden" dialog and no
  other detail - confirmed the hard way before this file's element order
  was fixed against a real, working example (cloudfoundry-community's
  `windows-stemcell-concourse` `autounattend.xml`) and Microsoft's own
  documented ordering
- Post-install phase (over WinRM once a guest address is known -
  `deployment.static_ip` directly for a static deployment, otherwise
  `deployment.guest_reported_ip`, captured from the callback request's own
  source address the moment it lands, `api/routes/callback.py` - and only
  as a last resort `HypervisorDriver.get_guest_ip()`, which needs VMware
  Tools installed in the guest to report anything at all and was the
  actual cause of a real deployment spinning here for the full
  `WINRM_REACHABILITY_MAX_ATTEMPTS` despite Setup and the callback having
  both already succeeded - this fallback still can't rely on Tools, since
  Tools isn't installed until the very next step, after a guest address is
  already known one way or another; authenticating as
  `template.local_admin_username`, not the now-disabled built-in
  Administrator): the WinRM-reachability wait right after resolving that
  address now logs immediately (`waiting for {ip} to become reachable
  over WinRM`) and every few attempts after (`still waiting... (Ns
  elapsed)`) - this loop previously ran completely silently for up to
  `WINRM_REACHABILITY_MAX_ATTEMPTS * WINRM_REACHABILITY_POLL_INTERVAL_SECONDS`
  (~10 minutes), indistinguishable in the log from an actual hang.
  Partway through that window (once, not every attempt - it's a real
  hypervisor API call) it also cross-checks `HypervisorDriver.
  get_guest_ip()` against the address it's been trying: DHCP *usually*
  renews the same lease across Setup's own final reboot into the
  running OS, but that's not guaranteed, and `guest_reported_ip` was
  likely captured early (the specialize-pass network ping above, which
  fires well before that reboot, or the real callback if it landed).
  If the hypervisor's own view disagrees, it switches to the new address
  (persisting it back to `guest_reported_ip` too, so a subsequent "Retry
  post-install" starts from the corrected value) and keeps going on the
  remaining attempts, rather than exhausting the whole budget against an
  address that may no longer mean anything - install VMware Tools first,
  if present (see above), then install every configured Windows feature
  in one call
  (`WinRMClient.install_features`, a single
  `Install-WindowsFeature -Name @(...) -IncludeManagementTools`, not one
  call per feature) - the same thing Server Manager's own "Add Roles and
  Features" wizard does (select several, click Install once), a single
  DISM/CBS transaction rather than N separate ones each with their own
  overhead. `-IncludeManagementTools` is always on, not conditional on
  the edition having a GUI: confirmed against Microsoft's own documented
  behavior, on Server Core it installs whatever's applicable
  (PowerShell/CLI tools) and silently skips the GUI-only pieces rather
  than failing, so a Desktop Experience template gets ADUC, Group Policy
  Management, the DNS/DHCP consoles, etc. right alongside whichever roles
  pull them in, matching what installing through Server Manager's GUI
  gives you by default. Before running it, `install_features` waits (up to
  120s) for the `TrustedInstaller` service to go idle - a demand-start
  service still busy right after first boot causes a transient
  `0x80070020` (`ERROR_SHARING_VIOLATION`) that a bare retry can't
  reliably outrun; `run_post_install` also keeps a 3-attempt/30s-apart
  retry loop around the whole call as a safety net for that same error.
  While it runs, the heartbeat that logs "still running (Ns elapsed)"
  every 30s also polls `WinRMClient.get_feature_install_status` - over a
  **separate** WinRM session, not the one running the install itself
  (sharing one session corrupts NTLM's per-session sequence counters,
  confirmed live as a `BadMICError` that crashed an entire install) - and
  appends which of the requested roles have already finished, e.g.
  `2/4 installed so far: DNS, AD-Domain-Services`. `install_features` also
  checks the structured `Success`/`RestartNeeded` fields
  `Install-WindowsFeature` itself returns, not just whether the command
  ran without throwing - those aren't the same thing, a feature set can
  report `Success=False` without ever raising a terminating error at all -
  and once it reports success, `verify_windows_features_installed` runs
  one explicit `Get-WindowsFeature` confirmation pass across every
  requested feature before anything else proceeds. If a restart was
  reported needed, one reboot happens right here, before continuing. If
  `template.enable_rdp` is on (the default: WinRM itself is
  deliberately closed for good once post_install finishes, so leaving RDP
  off too would mean no remote access at all afterward),
  `WinRMClient.enable_rdp` sets `fDenyTSConnections=0` and enables the
  built-in Remote Desktop firewall rule group by its locale-independent
  `Group` identifier (`@FirewallAPI.dll,-28752`), not the localized
  `DisplayGroup` text - matching on the English display name ("Remote
  Desktop") broke RDP enablement outright on non-English images, where
  that group is named differently (e.g. "Remotedesktop" on German) -
  neither needs a restart to take effect. Then install each configured
  app asset in order (the guest downloads each installer itself over
  `Invoke-WebRequest`, see the App Assets section above for the token/
  download flow and how each install actually runs and gets verified
  now), run each template post-install script in order, check Windows
  Update (if enabled for this template/deployment, see below), join the
  domain here if configured for `post_install` timing,
  reboot, disable the built-in Administrator if a custom admin account
  is configured, verify the guest comes back reachable. Every one of
  those (feature installs, RDP, app installs, post-install scripts, the
  domain join) runs through
  `_run_with_heartbeat`, not a bare blocking WinRM call: a real Windows
  role install can legitimately take several minutes, and without a
  periodic "still running (Ns elapsed)" log line the deployment log
  otherwise goes completely silent for however long that takes,
  indistinguishable from a hang - confirmed as a real point of confusion
  on an otherwise-successful deployment.
- **Windows Update** (`WinRMClient.install_windows_updates`), on by default,
  toggled off per template (`install_windows_updates`, template
  create/edit and "Customize installation", same `EffectiveTemplate`
  mechanism as every other overridable field) for deployments that need
  to be quick: searches via the built-in WUA COM API
  (`Microsoft.Update.Session`/`CreateUpdateSearcher().Search("IsInstalled=0
  and IsHidden=0")`), no `PSWindowsUpdate` module or PSGallery/internet
  access needed beyond what Windows Update itself already requires. Runs
  through the same one-shot SYSTEM-context Scheduled Task mechanism as
  app installs (`_run_as_system_task`, see the App Assets section above),
  not directly over the driving WinRM session: confirmed live that
  `CreateUpdateDownloader()` refuses a WinRM/NTLM network-logon token
  outright (`E_ACCESSDENIED`) - there's no way to search/download/install
  updates over that session at all without it. Skips any individual
  update over 150MB (`MaxDownloadSize`, known before downloading, not
  guessed) rather than downloading everything found - a deliberate,
  explicitly requested speed trade-off, not a bug: on Windows Server
  2019+ there's no separate small "security-only" patch anymore, the
  monthly Cumulative Update (usually the single largest item offered,
  often 500MB-1.5GB) *is* that month's security fix merged into one
  package, so this trade-off does mean skipping that month's OS security
  patch in favor of everything smaller (drivers, definitions, servicing
  stack updates, individual small patches) installing quickly. Progress
  is surfaced the same way feature installs are: a **separate** WinRM
  session polls a status-marker file the scheduled task's script updates
  as it moves through search/download/install phases (e.g. "installing 3
  update(s)"), read back into the deployment log's "still running (Ns
  elapsed)" heartbeat - an empty read means either the step hasn't
  started yet or it already finished, not an error, and a transient
  failure on that separate polling session just means one heartbeat tick
  shows no extra detail, it doesn't mean the install itself stalled,
  `Installer.Install()` inside the scheduled task's own script is a
  normal synchronous COM call that can legitimately run for many minutes
  with no way to report finer-grained progress than the phase marker
  already does. Best-effort: an update server hiccup or a failed install
  is logged as a WARN, never fails an otherwise-successful deployment.
  `WinRMClient`'s underlying `pywinrm` session is built with explicit,
  widened `operation_timeout_sec`/`read_timeout_sec` (60s/90s, not
  pywinrm's own 20s/30s defaults): WinRM's Receive operation can
  legitimately block server-side for up to the operation timeout waiting
  for output before replying, and under real load (a guest mid-Windows-
  Update is the confirmed case) that round trip can exceed a too-tight
  client read-timeout, surfacing as a `requests.ReadTimeout` that looks
  identical to the guest being unreachable but isn't - `_run_as_system_task`'s
  own poll loop (shared with app installs) also tolerates one such
  transient failure per iteration rather than aborting the whole
  operation on it, since the loop's own attempt ceiling already bounds
  how long a genuinely dead guest gets tolerated either way.
- Every reboot in this pipeline (feature-install, VMware Tools, and the
  final one) goes through the same `_reboot_and_wait`, which does two
  things worth knowing about: it requires 3 consecutive successful
  reachability checks, not just one, before considering the guest
  settled (a single successful probe answered fine once, then failed
  opening a new shell moments later on a real deployment - the reboot
  after a Windows Update install can leave the OS busy finishing
  servicing well past when basic connectivity returns), and each of
  those checks uses a brand-new `WinRMClient`, not the pre-reboot one
  reused throughout - a real reboot severs the TCP connection abruptly,
  and pooled HTTP connections can get stuck reusing a half-dead socket
  rather than opening a fresh one, which looked exactly like "the guest
  is reachable by every other means but this check won't agree" on a
  real deployment. It also captures the guest's own `LastBootUpTime`
  before triggering the reboot and again once reachable, logging both -
  proof a reboot actually happened rather than the guest simply staying
  reachable the whole time (a reboot command that silently doesn't fire
  looks identical to a working one under a reachability check alone,
  confirmed for real with `Restart-Computer` over WinRM before it was
  replaced with `shutdown.exe`).
- Feature installs and app installs both stay sequential, deliberately,
  not parallelized across concurrent WinRM sessions: Windows Feature
  installation already batches everything into one
  `Install-WindowsFeature` call (above), which is the real, safe speedup
  and the same one Server Manager's own wizard relies on - CBS
  (Component-Based Servicing) itself only ever runs one feature
  transaction at a time on a given machine regardless of how many
  separate WinRM sessions tried to start one, so multiple concurrent
  `Install-WindowsFeature` calls wouldn't actually run in parallel, they'd
  just contend for the same lock. App installs have the same shape for
  anything MSI-based: the Windows Installer service also only runs one
  transaction at a time system-wide, a second concurrent `msiexec` just
  fails with "another installation is already in progress" rather than
  actually running alongside the first. Non-MSI EXE installers could
  plausibly run concurrently, but not reliably enough across arbitrary,
  operator-supplied installers to build automation around by default.
  Then, as the very
  last WinRM action before marking `completed`, four things get cleaned
  up in a **single** call - checked against Microsoft's own
  `Disable-PSRemoting` docs, which explicitly list three of the four as
  manual steps it does not perform on its own: the custom `DeployCore
  WinRM` firewall rule (deleted via `netsh`, the same tool that created
  it - `Get-NetFirewallRule -DisplayName` isn't guaranteed to match a
  netsh-created rule); the built-in "Windows Remote Management
  (HTTP-In)" firewall rule that `winrm quickconfig` itself enables as a
  separate, documented side effect (disabled by its stable internal
  `-Name`, not the localized `-DisplayGroup`, the same lesson already
  learned for RDP); `LocalAccountTokenFilterPolicy`, set to `1` during
  the specialize pass when a custom admin account is configured, removed
  here rather than left weakening UAC's remote-token filtering for every
  local admin account indefinitely; and finally `Disable-PSRemoting`
  plus stopping/disabling the WinRM service itself (in a detached
  process a few seconds later, not inline, so the command reporting
  success back doesn't get cut off by the very channel it's closing).
  All four run as one command, not the firewall/registry cleanup
  logged separately followed by a swallowed `Disable-PSRemoting`: an
  earlier version treated only the latter as "may not report back
  before this severs the connection," but disabling the built-in
  `WINRM-HTTP-In-TCP` rule is exactly as capable of doing that, it's the
  very rule this session's own connection is using - splitting them
  meant a real chance of `_run_with_retry` burning several minutes
  retrying a command whose response could no longer come back over a
  connection its own first half had just cut, then permanently failing
  (every retry opening a fresh connection against a now-disabled rule)
  right at the last step of an otherwise fully-succeeded deployment.
  WinRM is not reachable at all on a completed deployment from this point
  on, by design - ongoing monitoring is left to the operator's own tooling
- Error tracing: every major pipeline step (rendering the answer file,
  uploading the Windows ISO and the answer-file floppy, creating the VM,
  attaching media, setting boot order, powering on, each post-install
  action) updates a current-step marker before it runs. If any step
  raises, the failure message states exactly which step failed, and the
  full Python traceback is captured as a separate error-level log line
- Cleanup: the answer-file floppy image (contains a plaintext local admin
  password) is deleted from the hypervisor datastore and from local disk
  on both success and failure; a failed deployment's partially-created VM
  is deleted before the deployment is marked `failed`
- Timeout: a background cron job force-fails any deployment stuck past its
  stage timeout (`os_install_timeout_minutes` setting, default 90, editable
  per organization from Settings) and runs the same cleanup. This is
  independent of, and a genuine safety net for, `worker/main.py`'s own
  per-job `timeout` overrides on `run_deployment`/`wait_for_callback`
  (arq's own default job timeout is 300 seconds, which silently killed
  `wait_for_callback` mid-poll on real deployments before this was
  caught and fixed - confirmed via the worker's own logs showing
  `wait_for_callback failed, TimeoutError` at exactly 300.00s on a
  deployment whose guest had, in fact, already called back successfully.
  `run_post_install` runs inside `wait_for_callback`'s own job execution,
  not a separately-enqueued one, so that ceiling has to cover both
  phases combined)
- Duplicate-delivery guard: arq is at-least-once, not exactly-once - a
  job can be redelivered and re-run from scratch while the original
  execution is still alive and working. Confirmed live: a redelivered
  `wait_for_callback` execution ran its entire "poll for the callback,
  fall back to WinRM reachability" sequence a second time - logging a
  duplicate "install callback did not arrive" - well after the first
  execution had already moved the deployment into `post_install`, then
  crashed trying to transition `post_install -> post_install` (not a
  valid forward transition, the state machine correctly rejects it).
  Both `wait_for_callback` and `run_post_install` now check the
  deployment's actual state before doing anything: `wait_for_callback`
  only proceeds if the deployment is still exactly `installing_os` (the
  one precondition `run_deployment` guarantees before ever enqueueing
  it), and `run_post_install` has the identical check as defense in
  depth, since it's also independently enqueued by the retry-post-install
  route. Anything else means a second, stale execution of the same
  nominal job arrived after the real one already made progress - logged
  as a no-op and skipped, rather than redoing (and crashing on) work
  that's already done or in progress elsewhere.
- Detail view: live pipeline-stage visualization, full state-transition
  history, streaming log output (Server-Sent Events, ~1s poll interval),
  and a "Download full log" button producing a plain-text file with the
  deployment's details, full state history, and every log line
- Retry, two forms: full retry from `pending` (any state, any stage),
  which always builds a fresh VM (the previous one, if any, is deleted
  first) - available once a deployment is `failed`. Post-install-only
  retry re-enters the pipeline directly against the *same* VM instead,
  and is only offered when there is one: `_fail` (provision.py) only
  deletes the VM for a failure before `post_install`/`configuring` -
  Windows Setup itself already succeeded by either of those stages, so
  there's a perfectly good, bootable install worth keeping rather than
  building an entire new VM to retry what's usually a one-line script or
  app-install fix (`POST .../deployments/{id}/retry-post-install`,
  `deployment_service.retry_post_install`). Either form of retry clears
  the deployment's prior log lines (not the state-transition history,
  which is the real audit trail and keeps every past attempt visible as
  its own row) - without it, a retried deployment's log just kept
  appending onto the previous failed attempt's lines with nothing
  marking where one ended and the next began, reading exactly like the
  retry had silently done nothing. The frontend also reopens a fresh
  event-stream connection on retry (`GET .../events?since=...`): that
  stream deliberately closes for good the first time it observes a
  transition into `completed`/`failed`, so without explicitly reopening
  one, a page left open across a retry would show the state flip back
  to `installing_os`/`pending` once (from the retry response's own
  refetch) and then simply never update again - `since` (an ISO
  timestamp, captured right after that refetch) tells the new connection
  to skip straight to new events instead of replaying everything the
  refetch already fetched over REST
- VM lifecycle (once a VM exists): live power state (read directly from the
  hypervisor, not cached), power on, shut down (graceful) or power off
  (hard). There's no dedicated "delete just the VM" action in the UI -
  remove it directly on the hypervisor if you want it gone without
  deleting the deployment record too. **Delete deployment** (admin-only)
  is a soft delete: it hides the deployment from lists/dashboard but
  never touches the hypervisor, any VM that exists is simply left running,
  untracked from that point on
- On an actual deployment *failure*, cleanup is automatic: the VM that
  failure's own deployment created is deleted on the hypervisor as part of
  marking the deployment `failed` (`driver.delete_vm`, worker's `_fail()`).
  A real deployment showed a folder always left behind on the datastore
  after delete, traced to VM creation itself: `vmPathName` was set to
  `"[datastore] name"` (a valid but ambiguous form whose exact resolution
  is left to the host's own default-naming logic), and this ESXi version
  resolved it one level too deep - `[datastore]/name/name/name.vmx`
  instead of the expected `[datastore]/name/name.vmx`. `Destroy_Task`
  correctly removes the VM's actual (inner) home directory on delete, but
  has no way to know an extra, empty wrapping folder exists one level
  above it. Fixed at the source: `vmPathName` now spells out the exact
  filename (`"[datastore] name/name.vmx"`), so only one folder gets
  created in the first place. `esxi.py`'s delete still follows up with an
  explicit, best-effort `DeleteDatastoreFile_Task` on the VM's folder path
  regardless, a no-op in the normal case, as insurance against the
  separate, genuinely rare file-lock race right after power-off that can
  still occasionally leave `Destroy_Task`'s own cleanup incomplete
- List view: filter by state or hostname, paginated, and exportable to CSV

### Notifications
- Per-user, in-app: a user is notified when a deployment they created
  starts, completes, or fails. Bell icon in the header, polled every 20
  seconds for an unread count; opening it shows recent notifications,
  clicking one marks it read and navigates to the linked deployment
- Optional email delivery via Microsoft 365 / Microsoft Graph, instance-wide
  configuration (Settings → Email notifications): tenant ID, an Entra ID
  app registration's client ID/secret (Mail.Send application permission),
  and a sender mailbox, with a "Send test email" button to verify the setup
  actually works before relying on it
- Optional Teams delivery, also instance-wide (Settings → Teams
  notifications), messaging the specific person who triggered the event
  directly via Microsoft Graph's Activity Feed API (`POST /users/{id}/
  teamwork/sendActivityNotification`) - a notification banner + Activity-tab
  entry, the documented app-only-auth-compatible way to notify one specific
  Teams user without hosting a full Bot Framework bot (a real 1:1 chat via
  `POST /chats` needs a second real user identity as the chat's other
  member, which a plain app registration can't be). Two real prerequisites
  beyond tenant/client ID/secret, both on the M365 tenant's own side:
  `TeamsActivity.Send` + `TeamsAppInstallation.ReadWriteForUser.All`
  application permissions admin-consented, and a Teams app already
  published to the org's app catalog whose manifest declares a custom
  activity type (`deploymentNotification`, `templateText: "{message}"`) -
  that app's catalog ID is the "Teams app ID" field. "Send test
  notification" surfaces Graph's own error text directly if either isn't
  set up right, rather than silently doing nothing
- Notification content itself - subject/body for email, message text for
  Teams - is fully editable per event (Settings → Notification content),
  not fixed strings: `{hostname}`/`{error}` placeholders (whichever apply
  to that event) get substituted in, an unknown or misspelled placeholder
  is left as literal text rather than breaking the send.
  `NotificationTemplate` rows are seeded with today's previously-
  hardcoded text so behavior doesn't change until someone edits one
- Per-user preferences (Account page): independently toggle email and Teams
  delivery, per channel, for deployment started / completed / failed.
  Defaults to complete and failed only on both channels, not every start,
  to avoid notification noise. Either channel works without the other
  being configured; both need the user to have an email address set
  (doubles as the Teams UPN)
- Both channels always send through a background job, never inline in a
  request or the provisioning pipeline, so a slow or failing Graph API call
  can never affect deployment outcome or page load time

### Webhooks
- Org-scoped, generic outbound webhooks, meant to be consumed by your
  ticketing/automation tool's own inbound-webhook trigger (Jira Automation,
  ServiceNow, Zapier, n8n, etc.) rather than DeployCore integrating any one
  of them natively
- Configure a URL, a signing secret, and which events to send: deployment
  started/completed/failed/retried, a completed deployment going
  unreachable
- Every delivery is POSTed as JSON (`{event, occurred_at, data}`) with an
  `X-DeployCore-Signature: sha256=<hmac>` header (HMAC-SHA256 over the
  raw body using your configured secret), the same convention GitHub and
  Stripe webhooks use, so most tools' built-in "verify signature" step
  works unmodified
- Delivered via a background job with up to 3 attempts (exponential
  backoff) on failure; a "Test" button sends a synthetic event immediately
  and shows the result inline; the last 20 deliveries (status code,
  success/fail, response snippet, timestamp) are visible per webhook

### Database backups
- Automatic daily `pg_dump` (custom format, compressed) via a background
  cron job, plus a "Run backup now" button, Settings → Backups
- Keeps the newest 14 backups, older ones are pruned automatically
- Download any backup as a file directly from the Settings page

### Settings
Hierarchical key/value store, four scopes: `global` < `org` < `template` <
`deployment`, resolved most-specific-first. Only `global` and `org` scopes
have UI/API surface today (`template`/`deployment` scopes exist in the data
model for future use). The per-organization deployment timeout has its own
dedicated, labeled field in the UI; an "Advanced" section underneath still
exposes the raw key/value form for anything else. Global panels (MSP
Organization, Updates, Email notifications, Backups) are laid out side by
side on wide screens instead of stacking, and each panel that has more than
one field to change (Email notifications, for example) has a single
"Apply changes" button for the whole panel instead of one per field. Known
keys in active use:
`instance_name`, `logo_filename`, `update_requested`/`update_status`/
`current_commit`/`latest_commit`/`commits_behind`/`checked_at`/
`git_available` (all global, managed by the self-update feature, not meant
to be hand-edited), `os_install_timeout_minutes` (global or org, default 90
if unset anywhere).

### Audit Log
Per-organization, append-only, paginated, exportable to CSV. Records action,
target type/ID, acting user, timestamp, and a JSON detail blob. Covers
login/logout/2FA changes/force-logout, user/organization/hypervisor/disk
layout/template/ISO asset/app asset/webhook create-update-delete, template
and disk layout export/import, settings changes (including logo, M365 config, and
self-update triggers), and deployment create/retry/power actions. For a
detailed per-deployment error trail instead of an audit trail, use that
deployment's log stream and "Download full log" button instead.

### Dashboard
- Per-organization: running/completed/failed deployment counts, hypervisor
  connection health, 8 most recent deployments, and a first-run "Getting
  started" checklist until the org has a hypervisor, a Windows ISO, and a
  template
- Cross-organization overview (global admins only): one row per
  organization with the same counts, click a row to switch the active
  organization
- The checklist waits for all 4 of its own data sources to load
  (`Promise.all`) before deciding whether to show itself: it used to
  decide off whichever ones had already resolved, so on every page
  load/refresh it would flash visible for an instant even for an org
  that had already completed every step, then disappear once the rest
  of the data caught up. Every list page across the app (Deployments,
  Templates, ISO/App Assets, Disk Layouts, Hypervisors, Users,
  Webhooks, Audit Log, Organizations) had the identical anti-pattern one
  level down - an empty fetched list and a still-in-flight one look
  identical to a plain `rows.length === 0` check, so "No results." would
  flash before real data arrived. `DataTable` now takes a `loading` prop
  that suppresses the empty message until the caller's fetch actually
  resolves. The org switcher itself had the same gap even earlier in the
  chain: every org-scoped page reads `selectedOrgId` from `OrgContext`,
  which starts `null` until the organization list itself has loaded, so
  "Select an organization first." would flash on load/refresh even with
  one already selected - `OrgContext` now exposes a `loaded` flag every
  org-scoped page's guard checks first

### Documentation
A built-in "Documentation" tab (`frontend/src/wiki`), available to every
signed-in user regardless of role, covers every configurable feature in the
app: setup and updating, users/roles/2FA/sessions, organizations (including
deleting one), hypervisors, ISO assets, app assets, disk layouts, templates
and Windows roles (including the AD DS limitation above, stated plainly
there too), deployments, troubleshooting a failed or stuck deployment,
in-app and email
notifications, webhooks, backups, self-update, the audit log, and branding.
Every article has a short "quick overview" (what it does, in plain language)
and a full "deep dive" underneath (exact fields, defaults, and edge cases),
searchable from a filter box in the sidebar. It's static content shipped with
the app, no network access needed to read it.

This (and this README) is deliberately where the detail lives, not the UI
itself: field labels stay to the point (a bare "Locale", not "Locale
(Windows id, not IETF)"), and confirmation dialogs state only what you need
to know before clicking (what's being deleted, the one fact that'd surprise
you, and that it can't be undone), not a full explanation of every
consequence. If a dialog or a label leaves something unclear, the matching
Wiki article (or the section of this README it came from) is where the full
answer is, not a longer dialog.

### Visual design
Blue accent color for primary actions/links/icons against a neutral light
theme by default, with a full dark mode (toggle in the header, remembers
your choice, and applied consistently across every page, including native
form controls like checkboxes and scrollbars via `color-scheme`). Scaled up
roughly 16% from a typical dense admin-UI baseline. Every dropdown in the
app is a fully custom-built listbox component, not a raw `<select>`, so it
can actually be restyled (and is) instead of falling back to the browser's
own unstyled popup; its option list renders through a portal so it's never
clipped by a scrollable ancestor (a table wrapper, for example) the way a
plain absolutely-positioned popup would be. File inputs are drag-and-drop
styled throughout too. Form validation is custom and inline (red text
under the field), not the browser's own "please fill in this field"
tooltip, and server-side validation errors (a password that's too short,
for example) are turned into one plain sentence instead of a raw error
payload. Destructive actions (deleting a hypervisor, disk layout, ISO,
template, webhook, VM, or logo; running an update) are all gated behind a
confirmation dialog.

DeployCore ships with its own default visual identity (an icon mark and
browser-tab favicon, `frontend/src/components/BrandMark.tsx` and
`frontend/public/favicon.svg`), shown in the sidebar and sign-in screen
until an MSP uploads their own logo (Settings → MSP Organization), which
then takes its place everywhere the same way. The sign-in and setup-wizard
screens, and the Dashboard behind its cards, all sit on the same subtly
animated background (soft blurred shapes drifting slowly, plus a network
of slowly-moving connected dots), matching the light/dark theme and
respecting `prefers-reduced-motion`.

## API reference

All routes are under `/api`. Auth is a JWT bearer token
(`Authorization: Bearer <token>`) except where noted. RBAC floor is the
minimum effective role for the request's organization unless marked
"(global)", meaning the floor applies instance-wide regardless of org.

| Method | Path | Floor | Notes |
|---|---|---|---|
| GET | `/api/setup/status` | none | `{needs_setup: bool}` |
| POST | `/api/setup` | none | one-shot; `409` if already set up |
| GET | `/api/instance` | none | `{name, has_logo}` |
| GET | `/api/instance/logo` | none | serves the logo file, `404` if unset |
| PUT/DELETE | `/api/settings/global/logo` | admin (global) | multipart upload; PNG/JPEG/SVG, 5 MB max |
| POST | `/api/auth/login` | none | rate-limited; returns a token, or `{requires_totp, ticket}` if 2FA is enabled |
| POST | `/api/auth/login/totp` | totp ticket | completes login with a 6-digit code |
| POST | `/api/auth/logout` | authenticated | revokes only the current session |
| POST | `/api/auth/logout-all` | authenticated | revokes every session for the caller |
| POST | `/api/auth/2fa/setup` | authenticated | returns a new secret + otpauth URL |
| POST | `/api/auth/2fa/confirm` | authenticated | verifies a code, enables 2FA |
| POST | `/api/auth/2fa/disable` | authenticated | verifies a code, disables 2FA |
| POST | `/api/auth/change-password` | authenticated | requires current password; revokes every session for the caller |
| GET | `/api/auth/me` | authenticated | current user + org-role map |
| GET/POST | `/api/organizations` | readonly / admin (global) | |
| GET/PATCH | `/api/organizations/{org_id}` | readonly / admin | |
| DELETE | `/api/organizations/{org_id}` | admin (global) | cascades to everything scoped to it, destructive |
| GET/POST | `/api/users` | admin (global) | |
| GET/PATCH | `/api/users/{user_id}` | admin (global) | PATCH `is_active` deactivates/reactivates |
| DELETE | `/api/users/{user_id}` | admin (global) | permanent; `400` for your own account; deployments they created are kept, unattributed |
| POST/DELETE | `/api/users/{user_id}/org-roles[/{org_id}]` | admin (global) | |
| POST | `/api/users/{user_id}/force-logout` | admin (global) | revokes every session for that user |
| POST | `/api/users/{user_id}/2fa/disable` | admin (global) | no code required, unlike the self-service version; doesn't revoke sessions |
| PUT/DELETE | `/api/users/me/avatar` | authenticated | multipart upload; PNG/JPEG, 2 MB max |
| GET | `/api/users/{user_id}/avatar` | authenticated | any user's profile picture, `404` if unset |
| GET/POST | `/api/organizations/{org_id}/hypervisors` | readonly / admin | |
| GET/PATCH/DELETE | `/api/organizations/{org_id}/hypervisors/{host_id}` | readonly / admin | |
| POST | `.../hypervisors/test-connection` | admin | ad-hoc, no saved host, tests values in the request body directly |
| POST | `.../hypervisors/{host_id}/test-connection` | admin | tests a saved host, enqueues a worker job, waits up to 20s |
| GET/POST | `/api/organizations/{org_id}/disk-layouts` | readonly / operator | |
| PATCH/DELETE | `.../disk-layouts/{layout_id}` | operator | org-owned only |
| GET | `.../disk-layouts/{layout_id}/export` | readonly | `{name, layout}` JSON |
| POST | `.../disk-layouts/import` | operator | creates a new org-scoped layout |
| POST | `/api/disk-layouts/global` | admin (global) | |
| GET/POST | `/api/organizations/{org_id}/iso-assets` | readonly / operator | |
| POST | `.../iso-assets/{iso_id}/chunk` | operator | raw body, one chunk |
| POST | `.../iso-assets/{iso_id}/finalize` | operator | assembles + checksums |
| DELETE | `.../iso-assets/{iso_id}` | operator | |
| GET/POST | `/api/organizations/{org_id}/app-assets` | readonly / operator | |
| POST | `.../app-assets/{app_id}/chunk` | operator | raw body, one chunk |
| POST | `.../app-assets/{app_id}/finalize` | operator | assembles + checksums |
| PATCH | `.../app-assets/{app_id}` | operator | name/kind/default_install_args only |
| DELETE | `.../app-assets/{app_id}` | operator | |
| GET | `/api/deployments/{deployment_id}/app-assets/{app_id}/download?token=...` | none (deployment token) | guest-initiated, not a user session |
| GET/POST | `/api/organizations/{org_id}/templates` | readonly / operator | |
| PATCH/DELETE | `.../templates/{template_id}` | operator | org-owned only |
| POST | `.../templates/{template_id}/clone` | operator | any visible template |
| GET | `.../templates/{template_id}/export` | operator | credentials excluded |
| POST | `.../templates/import` | operator | new disk layout row, no ISO, placeholder password |
| POST | `.../templates/{template_id}/preview` | operator | renders XML, no side effects |
| GET/POST | `/api/organizations/{org_id}/deployments` | readonly / operator | supports `state`, `q`, `limit`, `offset` query params |
| POST | `.../deployments/bulk` | operator | `{template_id, hypervisor_host_id, hostname_prefix, count}`, DHCP only |
| GET | `.../deployments/{deployment_id}` | readonly | |
| GET | `.../deployments/{deployment_id}/history` | readonly | state transitions |
| GET | `.../deployments/{deployment_id}/logs` | readonly | |
| GET | `.../deployments/{deployment_id}/answer-file` | readonly | the exact autounattend.xml this deployment shipped with; `404` if not rendered yet |
| GET | `.../deployments/{deployment_id}/events?since=...` | readonly | SSE stream; optional `since` (ISO timestamp) skips replaying events at/before it, closes for good after a `completed`/`failed` transition |
| POST | `.../deployments/{deployment_id}/retry` | operator | only from `failed`, always builds a fresh VM |
| POST | `.../deployments/{deployment_id}/retry-post-install` | operator | only from `failed` with a preserved VM (see Retry above) |
| GET | `.../deployments/{deployment_id}/power` | readonly | live hypervisor query |
| POST | `.../deployments/{deployment_id}/power/on` | operator | |
| POST | `.../deployments/{deployment_id}/power/off` | operator | body `{hard: bool}` |
| DELETE | `.../deployments/{deployment_id}` | admin | soft delete, any stage; doesn't touch a VM if one still exists, or cancel the pipeline if still running; `/history` and `/logs` above stay reachable afterward |
| POST | `/api/callback/{deployment_token}` | single-use token | called by the guest VM, not a user |
| GET/POST | `/api/organizations/{org_id}/webhooks` | admin | credential-bearing, admin only |
| PATCH/DELETE | `.../webhooks/{webhook_id}` | admin | |
| POST | `.../webhooks/{webhook_id}/test` | admin | sends a synthetic event immediately |
| GET | `.../webhooks/{webhook_id}/deliveries` | admin | last 20 |
| GET | `/api/notifications` | authenticated | own notifications, paginated (`limit`/`offset`) |
| GET | `/api/notifications/unread-count` | authenticated | polled by the bell icon |
| POST | `/api/notifications/{id}/read` | authenticated | |
| POST | `/api/notifications/read-all` | authenticated | |
| DELETE | `/api/notifications` | authenticated | deletes all of the caller's own notifications |
| GET/PUT | `/api/notification-preferences` | authenticated | self only, lazily created with defaults |
| GET/PUT | `/api/organizations/{org_id}/settings[/{key}]` | readonly / admin | |
| GET/PUT | `/api/settings/global[/{key}]` | admin (global) | |
| GET/PUT | `/api/settings/global/m365` | admin (global) | secret is write-only |
| POST | `/api/settings/global/m365/test` | admin (global) | sends a test email to the caller |
| GET | `/api/settings/global/backups` | admin (global) | |
| POST | `/api/settings/global/backups/run` | admin (global) | enqueues an immediate backup |
| GET | `/api/settings/global/backups/{filename}` | admin (global) | downloads a backup file |
| GET | `/api/settings/global/update/status` | admin (global) | current commit, commits behind, live stage if updating |
| POST | `/api/settings/global/update/check` | admin (global) | forces an immediate check (`git fetch` + recompute commits behind), doesn't update |
| POST | `/api/settings/global/update/run` | admin (global) | triggers a self-update; `400` if one is already running |
| GET | `/api/settings/global/tls` | admin (global) | current mode plus uploaded certificate subject/expiry, if any |
| PUT | `/api/settings/global/tls/certificate` | admin (global) | multipart upload (`cert_file`, `key_file`); validated pair, switches to it |
| PUT | `/api/settings/global/tls/mode` | admin (global) | `{value: "self_signed" \| "uploaded"}`; `400` for `"uploaded"` with nothing on file |
| GET | `/api/organizations/{org_id}/audit-log` | readonly | paginated (`limit`/`offset`) |
| GET | `/api/dashboard/overview` | admin (global) | |
| GET | `/api/health` | none | `{status: "ok"}` |

## Environment variables

Set in `.env` (loaded by all containers via `env_file`). `scripts/setup.sh`
fills in `APP_SECRET_KEY` for you.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_SECRET_KEY` | yes | none | Fernet key for credential encryption and JWT signing |
| `DATABASE_URL` | yes | none | `postgresql+asyncpg://...` |
| `REDIS_URL` | yes | none | `redis://...`, shared by arq, the login rate limiter, and session tracking |
| `APP_PUBLIC_URL` | no | `http://localhost:8000`, auto-set to this host's detected LAN IP by `scripts/setup.sh` | Base URL guest VMs use to reach `/api/callback` and app-asset downloads; must be reachable from provisioned VMs (not `localhost`), and stays plain `http://` on port 8000, bypassing the HTTPS proxy on purpose (see "HTTPS certificate") |
| `ISO_STORAGE_PATH` | no | `/data/isos` | Permanent ISO and logo storage inside the `api`/`worker` containers |
| `ISO_BUILD_TMP` | no | `/data/iso_build_tmp` | Scratch space for answer-file floppy builds and in-progress ISO uploads |
| `APP_ASSET_STORAGE_PATH` | no | `/data/app_assets` | Permanent MSI/EXE installer storage, `api` container only (the worker never touches these bytes directly, the guest downloads them itself) |
| `APP_ASSET_BUILD_TMP` | no | `/data/app_asset_build_tmp` | Scratch space for in-progress app asset uploads |
| `BACKUP_DIR` | no | `/data/backups` | Where database backups are written and served from |
| `TLS_CERTS_PATH` | no | `/data/tls` | Where an uploaded HTTPS certificate/key pair is stored, shared with the `proxy` container |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | no | `deploycore` / `deploycore` / `deploycore` | Postgres container credentials |
| `TURN_HOST` | no | `localhost`, auto-set to the detected LAN IP by `scripts/setup.sh` | Address the browser and agents use to reach coturn (STUN/TURN) for Shadow's WebRTC path; only matters for a host that isn't on this server's own LAN - ICE always tries a direct connection first |
| `TURN_USERNAME` / `TURN_PASSWORD` | no | `deploycore` / generated by `scripts/setup.sh` | Static long-term coturn credentials, shared by every agent and browser session |
| `REMOTE_AGENT_MSI_URL` | no | the repo's `agent-latest` release asset | Where DeployCore auto-fetches the agent `.msi` from on startup to seed the global Remote Agent asset; set empty to disable (air-gapped, upload by hand) |
| `PROJECT_DIR` | no | auto-detected | Only needed if the `updater` container's automatic self-discovery of this repo's host path doesn't work in your Docker setup; overrides it with an absolute host path when set |

## Development

| Command | Effect |
|---|---|
| `./scripts/setup.sh` / `make install` | One-time install: `.env` + secret key + build + start |
| `make dev` | `docker compose up --build` (foreground) |
| `make update` | Manual equivalent of the Settings "Update now" button |
| `make migrate` | Runs Alembic migrations manually (rarely needed, `api` does this on every startup already) |
| `make test` | Runs the backend test suite |
| `make seed` | Runs `scripts/seed.py` |
| `make down` | `docker compose down` (keeps volumes) |

The `api` container runs with `--reload` and picks up backend code changes
automatically. The `worker`, `updater`, and `proxy` containers do not
hot-reload, run `docker compose restart worker` (or `updater`/`proxy`) after
changing anything they import. `proxy` does pick up a `tls_mode` setting
change (uploading a certificate, switching back to self-signed) on its own
within a few seconds, without a restart, that's a routine runtime behavior,
not a code change.

Tests run against a dedicated `deploycore_test` database that the test suite
creates and tears down itself (`backend/tests/conftest.py`), never against
the real `deploycore` database `make dev` uses, so running `make test`
can't affect real data.

Tests cover: RBAC enforcement (every mutating route rejected below its
floor), autounattend.xml rendering (domain-join present/absent/deferred,
disk layout variants including the recovery partition), deployment state
machine (every legal/illegal transition, retry semantics), Fernet credential
round-trip, answer-file floppy builder temp-directory cleanup on both
success and subprocess failure, deployment/hypervisor/audit-log
org-scoping, global vs org-scoped ISO assets, organization deletion
(cascades correctly, audit log survives it, global-admin only), and HTTPS
certificate/key pair validation
(matching key rejected if it doesn't, expired certificates rejected,
garbage input rejected).

## Uninstalling

To completely remove DeployCore and every trace of its data from this host:

```bash
docker compose down -v --rmi local
cd ..
rm -rf deploycore
```

- `docker compose down -v --rmi local` stops and removes every container in
  the stack, plus its named volumes (`postgres_data`, `iso_storage`,
  `iso_build_tmp`, `db_backups`, `tls_certs`, `caddy_data`) and the images
  this repo builds locally (`api`/`worker`/`frontend`/`proxy`/`updater`).
  This is what actually deletes the database, so every organization, user,
  deployment record, uploaded ISO, and backup goes with it, there is no
  undo once this runs.
- `rm -rf deploycore` (from the parent directory, adjust the path if you
  cloned it somewhere else or under a different name) removes the cloned
  repository itself, including your `.env` file and its `APP_SECRET_KEY`.
- Nothing outside this repo's own containers/volumes/directory is touched:
  DeployCore never modifies your ESXi hosts, and deleting it does not
  delete any VM it already created there, those keep running exactly as
  they are until you remove them yourself directly on ESXi, if you want
  them gone too.
- If you only want to reset the app back to a fresh setup wizard while
  keeping the rest of the host alone, `docker compose down -v` without
  `--rmi local` is enough, it drops all data but leaves the built images in
  place so the next `docker compose up -d` is fast.

## Known limitations

- ESXi is the only hypervisor target - the only one this project aims to
  support.
- Installing "Active Directory Domain Services" from a template only runs
  `Install-WindowsFeature AD-Domain-Services`, i.e. it installs the role
  binaries. It does **not** promote the server to a domain controller and
  does **not** create or configure any Group Policy Objects, OUs, or
  delegation, none of that is automated today. If you need an actual
  domain controller, run `Install-ADDSForest`/`Install-ADDSDomainController`
  (or the classic `dcpromo` flow) and configure GPOs through GPMC yourself
  after the server comes up.
- No VM lifecycle beyond create/power-on/power-off/delete (no snapshots,
  migration, resize, clone-as-VM).
- Templates cover the ESXi VM-creation options actually worth setting per
  template (CPU/cores-per-socket, RAM, disk size and provisioning type,
  network name and adapter type); things like VM hardware version, CPU/MMU
  virtualization mode, reservations/limits, or VM encryption aren't exposed
  and use ESXi's own defaults.
- No LDAP/SSO, local username+password accounts only (with optional TOTP
  2FA).
- Notifications: in-app is always on; email requires configuring Microsoft
  365/Graph yourself, no other provider is supported.
- Self-update has no automatic rollback: if a migration or restart fails
  partway through, the containers already running keep running on whatever
  they had, check the Settings page's error message and the container logs.
  It also requires `git pull` to work without a credential prompt (public
  repo, or a credential helper/SSH key already set up on the host); if it
  can't (or this isn't a git checkout at all), the feature disables itself
  with a clear message instead of failing silently.
- No tunnel/relay networking, DeployCore assumes it already has a routable
  path to every hypervisor and every hypervisor's guest network.
- Linux provisioning and PXE are out of scope entirely; the whole pipeline
  is Windows-answer-file-ISO specific.
- Audit logging covers most mutating actions but not literally every one
  (e.g. individual ISO chunk uploads aren't logged, only create/finalize).
- Login access tokens can be revoked (logout/logout-all/force-logout), but
  there's no way to list active sessions individually, only revoke all of
  them at once.
