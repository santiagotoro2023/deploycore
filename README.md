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
automatically. The page shows live progress (Pulling → Building →
Restarting → Done) and the app is only unreachable for the last part of that,
usually under a minute. Nothing in your database is touched by an update
itself.

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
  force-logout (revokes every active session for that user immediately)
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
- Per-organization. ESXi only for now, selectable in the UI. (A Proxmox
  driver is stubbed in the codebase for possible future support, but it
  isn't wired into any user-facing surface yet, every method it has raises
  `NotImplementedError`.)
- Fields: name, API endpoint, username, credential (write-only, never
  returned by the API after creation), TLS verification toggle, default
  datastore (used when creating a VM if nothing more specific is set). The
  network a VM's NIC attaches to is defined per-template instead (see
  Deployment Templates below), not on the hypervisor connection
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
- The layout created automatically during setup enables the recovery
  partition (EFI 100 MB, MSR 16 MB, recovery 1000 MB, OS volume remaining)
- Rendered directly into the generated `autounattend.xml`'s
  `<DiskConfiguration>` block
- Create/edit (operator+) for org-scoped layouts; a separate global-create
  endpoint exists for admins but has no dedicated UI form yet
- Export (readonly+, downloads a JSON file) and import (operator+, uploads
  that JSON as a new org-scoped layout), and a delete button (operator+)

### ISO Assets
- Org-scoped or global. Windows Server ISOs only, uploaded through the UI
  as `windows_iso`. (A `virtio_iso` kind also exists in the data model for
  the stubbed Proxmox driver above, but nothing in the UI offers it today.)
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
  own chunk/finalize/delete routes for global ones, admin-only)
- An MSI or EXE installer (`kind`), a display name (independent of the
  uploaded filename, e.g. "Datto RMM Agent" vs. `AgentSetup_1.2.3.exe`),
  and default silent-install arguments (e.g. `/qn /norestart` for an MSI,
  or whatever an EXE's own convention is, commonly `/S`/`/silent`/
  `/verysilent`/`/quiet`); a template can override the arguments per
  attachment without touching the asset itself
- Attached to a template's `app_installs` (ordered list of
  `{app_asset_id, install_args}`), installed over WinRM during
  post_install, after Windows features and before post-install scripts
  (so a script can assume an app installed earlier in the list is already
  there). Not a foreign key: `app_installs` is a JSONB column, a deleted
  app asset is skipped with an error log line at deploy time rather than
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
- MSI installs run through `msiexec /i "<path>" <args>`; EXE installs run
  the downloaded file directly with `<args>` passed straight through.
  Exit code 3010 (success, reboot required) counts as success alongside 0

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
  disk layout, CPU count and cores per socket, RAM (MB), disk size (GB) and
  disk provisioning type (thin / thick lazily zeroed / thick eagerly
  zeroed), network name (an ESXi/vCenter port group, network segmentation
  is expected to already be handled by picking the right port group, not
  by a VLAN tag on the template) and network adapter type (VMXNET3, E1000,
  or E1000E), locale/timezone/keyboard layout as Windows
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
  order after both). Each role is installed
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
  of synthetic Enter keypresses right after power-on as a safety net for
  ISOs where the swap wasn't possible (media without that second boot
  image), plus a second, sparser round over the next couple of minutes for
  Windows Setup's own windowsPE-stage language/time/keyboard screen, which
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
  There is currently **no auto-logon mechanism at all**: two
  different attempts at it have each broken Setup outright on real
  hardware, both fully reverted. First, the declarative `AutoLogon`
  element (oobeSystem pass) - auto-logging in as whichever account
  `local_admin_username`/`local_admin_password` resolve to, so
  `FirstLogonCommands` could run with nobody at the console. Every
  deployment that included it failed during Setup regardless of where in
  oobeSystem it was placed; static vs. DHCP networking, hostname length,
  and a separate OOBE addition were all independently ruled out via
  controlled tests, isolating the element itself, confirmed when a real
  deployment finally completed once it was removed. Second, a
  specialize-pass `Microsoft-Windows-Deployment`/`RunSynchronousCommand`
  writing the same registry values (`AutoAdminLogon`, `DefaultUserName`,
  `DefaultPassword`, `AutoLogonCount`) a working `AutoLogon` element would
  itself write, via `reg.exe` (a first attempt used `powershell.exe`
  instead and crashed Setup a different way, "the computer was
  unexpectedly restarted", consistent with a documented failure mode
  where PowerShell cmdlets crash this early in Setup because WMI/CIM
  isn't fully initialized yet - `reg.exe` has no such dependency).
  Reasonable in theory (Winlogon reads those registry values the same way
  regardless of which mechanism wrote them, so a different Setup code
  path should have sidestepped whatever the element itself was hitting),
  but it didn't: a clean, isolated test (OOBE settings already reverted,
  only this component present) failed with the exact same WINDEPLOY
  0x80220005 error as the element did. The one deployment that's actually
  completed had neither mechanism present. Until something is found that
  avoids whatever both of these hit, `FirstLogonCommands` only run once a
  human physically logs in at the console, since `SkipMachineOOBE`/
  `SkipUserOOBE` alone don't make Setup log in on their own. If
  `template.custom_admin_enabled` is on (off by default), two
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
  (drive kept, emptied), removes the floppy device outright, and deletes
  the per-deployment answer-file floppy from the datastore, all
  best-effort, never worth failing an otherwise-successful deployment over
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
  here - deterministic, no enumeration-order guesswork left
- Post-install phase (over WinRM once the guest reports an IP, its static
  address directly for a static deployment, authenticating as
  `template.local_admin_username`, not the now-disabled built-in
  Administrator): install each configured Windows feature, install each
  configured app asset in order
  (the guest downloads each installer itself over `Invoke-WebRequest`, see
  the App Assets section above for the token/download flow, then runs it
  silently and deletes it), run each post-install script in order, join
  the domain here if configured for `post_install` timing, reboot, verify
  the guest comes back reachable, then as the very last WinRM action before
  marking `completed`: remove the WinRM firewall rule, `Disable-PSRemoting`,
  and stop+disable the WinRM service itself (the service stop runs in a
  detached process a few seconds later, not inline, so the command reporting
  success back doesn't get cut off by the very channel it's closing).
  WinRM is not reachable at all on a completed deployment from this point
  on, by design, see the health check entry below for what that means for
  ongoing monitoring
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
  per organization from Settings) and runs the same cleanup
- Post-deploy health check: a cron job runs every 15 minutes against every
  completed deployment with a known VM, and checks a plain TCP connect to
  port 3389 (RDP, on by default on every Windows Server SKU) on its guest
  IP, not WinRM: WinRM is deliberately closed for good once a deployment
  completes (see above), so this only proves the guest OS is up and
  reachable on the network, not that remote management still works,
  nothing does, on purpose. Records the latest reachable/unreachable
  status, keeps a 30-day append-only history shown as a strip of badges on
  the deployment detail page (a deployment that goes from healthy to
  unreachable also triggers a notification/webhook, see below)
- Detail view: live pipeline-stage visualization, full state-transition
  history, streaming log output (Server-Sent Events, ~1s poll interval),
  health status + history, and a "Download full log" button producing a
  plain-text file with the deployment's details, full state history, and
  every log line
- Retry: full retry from `pending` (any state, any stage), available once
  a deployment is `failed`; safe because nothing is reused, a fresh VM is
  always created
- VM lifecycle (once a VM exists): live power state (read directly from the
  hypervisor, not cached), power on, shut down (graceful) or power off
  (hard), delete VM (admin-only, deletes the VM on the hypervisor but
  keeps the deployment record and its full history/log for audit)
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
- Per-user preferences (Account page): independently toggle email delivery
  for deployment started / completed / failed / a completed deployment
  going unreachable. Defaults to complete and failed only, not every start
  or every health check, to avoid inbox noise
- Email delivery always happens through a background job, never inline in
  a request or the provisioning pipeline, so a slow or failing mail send
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
| GET | `/api/auth/me` | authenticated | current user + org-role map |
| GET/POST | `/api/organizations` | readonly / admin (global) | |
| GET/PATCH | `/api/organizations/{org_id}` | readonly / admin | |
| DELETE | `/api/organizations/{org_id}` | admin (global) | cascades to everything scoped to it, destructive |
| GET/POST | `/api/users` | admin (global) | |
| GET/PATCH | `/api/users/{user_id}` | admin (global) | PATCH `is_active` deactivates/reactivates |
| DELETE | `/api/users/{user_id}` | admin (global) | permanent; `400` for your own account; deployments they created are kept, unattributed |
| POST/DELETE | `/api/users/{user_id}/org-roles[/{org_id}]` | admin (global) | |
| POST | `/api/users/{user_id}/force-logout` | admin (global) | revokes every session for that user |
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
| GET | `.../deployments/{deployment_id}/health-history` | readonly | last 200 health checks |
| GET | `.../deployments/{deployment_id}/events` | readonly | SSE stream |
| POST | `.../deployments/{deployment_id}/retry` | operator | only from `failed` |
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

- ESXi is the only working hypervisor target. A Proxmox driver exists in
  the codebase as a stub for possible future support (every method raises
  `NotImplementedError`), but it isn't exposed anywhere in the UI.
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
- The post-deploy health check keeps a 30-day history, not indefinite.
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
