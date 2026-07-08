# DeployCore

DeployCore turns "provision a Windows Server VM" from a manual, error-prone
routine into a few clicks. Point it at your ESXi hosts, upload a Windows
Server ISO once, define what a server should look like (disk layout, roles,
domain membership), and then create new servers on demand, each one a real,
fresh Windows Setup install, not a cloned image. It's built for MSPs and IT
teams running multiple customer environments side by side: every customer
gets their own isolated organization, its own hypervisors, its own templates,
its own audit trail, all inside one instance with role-based access control.

If you've used Foreman for Linux/PXE provisioning, this is the same idea,
narrowed to Windows Server and built around answer-file ISOs instead of PXE.

## Quickstart

Requirements: Docker Engine + Docker Compose v2, and network access from
this host to your ESXi hosts.

```bash
git clone https://github.com/santiagotoro2023/provisioning.git deploycore
cd deploycore
./scripts/setup.sh
```

That's it. The script copies `.env.example` to `.env`, generates a secret key
for you, and builds and starts the whole stack. Open `http://localhost:5173`
and you'll land on a two-step setup wizard: name your instance, then create
your first administrator account. Everything else (database schema, etc.) is
handled automatically, including on every future update (see "Updating"
below).

One setting worth checking before you provision real VMs:
`APP_PUBLIC_URL` in `.env`. This is the address your ESXi guest VMs call back
to once Windows Setup finishes, it needs to be reachable from your customer
networks, not just from your own laptop. `localhost` only works if DeployCore
and the VM happen to share a network where that resolves, which is rare in
practice. Set it to this host's real, routable address, then
`docker compose up -d` to pick up the change.

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
   a server: which ISO, which disk layout, CPU/RAM/disk size, network
   settings, local administrator password, optional domain join, and which
   Windows roles to install, picked from checkboxes (AD Domain Services,
   DNS, DHCP, IIS, File Server, Print Services, RD Session Host, DFS,
   Hyper-V, WDS).
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
how many commits. Click **Update now** and DeployCore pulls the latest code
from GitHub, rebuilds, runs any new database migrations, and restarts itself,
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
don't fully trust. If you'd rather not run it at all, remove the `updater`
service from `docker-compose.yml` and update by hand instead:

```bash
git pull
docker compose up -d --build
```

(same effect: `make update` runs exactly this)

Migrations always run automatically on `api` container startup regardless of
which update path you use, so there is never a separate manual migration
step.

## Roles and multi-tenancy

Three roles, ordered `admin > operator > readonly`. A user has a
`global_role` (applies everywhere) and/or per-organization roles (Users page
→ assign org role). The effective role for a request is the higher of the
two for that organization. `none` (the default for a newly created user with
no explicit role) grants no access anywhere.

| Role | Can do |
|---|---|
| `readonly` | View everything in organizations they're scoped to |
| `operator` | Everything `readonly` can, plus: create/retry/bulk-create deployments, power on/shut down/power off a deployment's VM, create/edit/delete disk layouts, templates, and ISO assets, clone/export/import templates and disk layouts |
| `admin` | Everything `operator` can, plus: create/edit organizations, manage hypervisor hosts and webhooks (including credentials/secrets) and run their test buttons, delete a deployment's VM, edit organization/global settings, manage users and their org-role assignments (global-admin only), rename the instance and manage its logo (global-admin only), configure M365 email and trigger backups/updates (global-admin only) |

RBAC is enforced server-side on every route (a dependency resolves the
caller's effective role for the request's organization and returns `403`
below the floor), the UI hiding a button is a convenience, not the
enforcement point.

Every `Organization` is an independent tenant: its own hypervisors,
templates, disk layouts, ISO assets, deployments, webhooks, settings, and
audit log. There is no separate "MSP organization" entity, the instance
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
  left blank, builds and starts everything
- Database migrations run automatically on every `api` container start
  (`backend/entrypoint.sh`), no manual step for first install or later
  updates
- Self-update: Settings → Updates shows current commit and how many commits
  behind `origin` you are (refreshed automatically every 5 minutes by the
  `updater` service), one button pulls, rebuilds, migrates, and restarts,
  with a live staged progress indicator. Falls back cleanly (a clear
  "self-update unavailable" message) if this isn't a git checkout or
  `PROJECT_DIR` isn't configured
- Instance name and, if set, a logo shown together (never one instead of
  the other) in the sidebar and sign-in screen; both editable from
  Settings → MSP Organization (global-admin only). Logo accepts PNG, JPEG,
  or SVG up to 5 MB
- Dashboard shows a "Getting started" checklist (add a hypervisor, upload a
  Windows ISO, create a template) for any organization that hasn't
  completed first-time setup yet, disappears once all three exist

### Security
- Username + password (argon2 hashing), with a minimum password policy
  (8+ characters, at least one letter and one digit) enforced on account
  creation and password changes
- Optional TOTP two-factor authentication per user (Account page): enroll
  with any standard authenticator app, login then requires a second-step
  code
- Session revocation: every login is tracked in Redis, not just a stateless
  JWT. "Sign out everywhere" (Account page) or an admin's "force logout"
  action on another user (Users page) immediately invalidates every
  outstanding token for that account, not just the current one
- Login rate-limited to 10 attempts / 5 minutes per source IP and per
  username

### Organizations
- List (scoped to what the caller can see: all orgs for a global-role user,
  else only orgs they have an explicit role in), create, view, edit
  (name/description/active flag), no delete (deactivate instead)

### Users
- Global admin only: list, create (username/password/display name/optional
  email/global role), edit (display name/global role/active flag/password),
  assign or remove a per-organization role, force-logout (revokes every
  active session for that user immediately)
- Users are identified and log in by username, not email. Email is optional,
  used only for M365 notification delivery if configured (see below)

### Hypervisors
- Per-organization. Type `esxi` (implemented) or `proxmox` (registers, but
  every operation raises "not implemented", no Proxmox provisioning yet)
- Fields: name, API endpoint, username, credential (write-only, never
  returned by the API after creation), TLS verification toggle, default
  datastore, default network
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
- Org-scoped or global. Two kinds: `windows_iso`, `virtio_iso` (the latter
  only matters once Proxmox is implemented)
- Chunked upload from the browser (8 MB chunks over sequential POSTs, then
  a finalize call that assembles the file, computes its SHA-256, and marks
  it `complete`), built for multi-gigabyte ISOs without loading the whole
  file into memory client- or server-side
- Delete removes both the database row and the file on disk

### Deployment Templates
- Org-scoped or global (global templates are inherited read-only by every
  org and can be cloned into an org-scoped copy)
- Fields: name, Windows ISO (nullable, a template can exist before an ISO
  is attached; the pipeline refuses to deploy from it until one is set),
  disk layout, CPU count, RAM (MB), disk size (GB), network name, VLAN ID,
  locale/timezone/keyboard layout, local administrator password
  (write-only), optional domain join (FQDN, join account, join credential
  [write-only], target OU, and timing, `answer_file` bakes the join into
  the unattended install, `post_install` joins afterward over WinRM),
  Windows roles/features picked as checkboxes from a curated common-roles
  list (AD Domain Services, DNS, DHCP, Web Server (IIS), File Server, Print
  Services, Remote Desktop Session Host, DFS Namespace, DFS Replication,
  Hyper-V, WDS), list of post-install PowerShell scripts (name + script
  text, run in order after roles)
- Create/edit/delete (operator+); editing a password/credential field blank
  leaves the stored value unchanged. Editing a template only affects
  deployments created from it afterward, deployments already completed or
  in progress are unaffected (shown as a note directly in the edit form)
- Clone: duplicates any visible template (own org's or an inherited global
  one) into a new org-scoped copy named "<name> (copy)", including
  encrypted credentials (copied as ciphertext, not re-entered)
- Export (operator+, JSON file, credentials excluded, disk layout inlined,
  ISO recorded only as an informational filename/kind hint) and import
  (operator+, recreates the disk layout as a new row, leaves the ISO
  unattached, sets a random placeholder local administrator password that
  must be replaced before the template can deploy)
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
- Pipeline (runs in the background worker, not the request thread): renders
  the answer file, builds a per-deployment answer-file ISO, uploads the
  Windows ISO and the answer-file ISO (and, for Proxmox once implemented, a
  VirtIO ISO) to the hypervisor datastore, creates the VM (UEFI firmware,
  PVSCSI controller on ESXi), attaches media, powers on. The guest's
  `FirstLogonCommands` enable WinRM and call back to
  `/api/callback/{token}` (single-use per-deployment token) once Windows
  Setup finishes, which is what advances `booting → installing_os`
- Post-install phase (over WinRM once the guest reports an IP): apply
  static network config if requested, install each configured Windows
  feature, run each post-install script in order, join the domain here if
  configured for `post_install` timing, reboot, verify the guest comes back
  reachable, then mark `completed`
- Error tracing: every major pipeline step (rendering the answer file,
  uploading each ISO, creating the VM, attaching media, setting boot order,
  powering on, each post-install action) updates a current-step marker
  before it runs. If any step raises, the failure message states exactly
  which step failed, and the full Python traceback is captured as a
  separate error-level log line
- Cleanup: the answer-file ISO (contains a plaintext local admin password)
  is deleted from the hypervisor datastore and from local disk on both
  success and failure; a failed deployment's partially-created VM is
  deleted before the deployment is marked `failed`
- Timeout: a background cron job force-fails any deployment stuck past its
  stage timeout (`os_install_timeout_minutes` setting, default 90, editable
  per organization from Settings) and runs the same cleanup
- Post-deploy health check: a cron job runs every 15 minutes against every
  completed deployment with a known VM, WinRM-pings its guest IP, records
  the latest reachable/unreachable status, and keeps a 30-day append-only
  history shown as a strip of badges on the deployment detail page (a
  deployment that goes from healthy to unreachable also triggers a
  notification/webhook, see below)
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
exposes the raw key/value form for anything else. Known keys in active use:
`instance_name`, `logo_filename`, `update_requested`/`update_status`/
`current_commit`/`latest_commit`/`commits_behind`/`checked_at`/
`git_available` (all global, managed by the self-update feature, not meant
to be hand-edited), `os_install_timeout_minutes` (global or org, default 90
if unset anywhere).

### Audit Log
Per-organization, append-only, paginated, exportable to CSV. Records action,
target type/ID, acting user, timestamp, and a JSON detail blob. Covers
login/logout/2FA changes/force-logout, user/organization/hypervisor/disk
layout/template/ISO asset/webhook create-update-delete, template and disk
layout export/import, settings changes (including logo, M365 config, and
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

### Visual design
Blue accent color for primary actions/links/icons against a neutral light
theme by default, with a full dark mode (toggle in the header, remembers
your choice). Scaled up roughly 10% from a typical dense admin-UI baseline.
Styled dropdowns and drag-and-drop-styled file inputs throughout, no raw
unstyled `<select>`/`<input type=file>` elements anywhere in the app.
Destructive actions (deleting a hypervisor, disk layout, ISO, template,
webhook, VM, or logo; running an update) are all gated behind a confirmation
dialog.

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
| GET/POST | `/api/users` | admin (global) | |
| GET/PATCH | `/api/users/{user_id}` | admin (global) | |
| POST/DELETE | `/api/users/{user_id}/org-roles[/{org_id}]` | admin (global) | |
| POST | `/api/users/{user_id}/force-logout` | admin (global) | revokes every session for that user |
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
| GET | `.../deployments/{deployment_id}/health-history` | readonly | last 200 health checks |
| GET | `.../deployments/{deployment_id}/events` | readonly | SSE stream |
| POST | `.../deployments/{deployment_id}/retry` | operator | only from `failed` |
| GET | `.../deployments/{deployment_id}/power` | readonly | live hypervisor query |
| POST | `.../deployments/{deployment_id}/power/on` | operator | |
| POST | `.../deployments/{deployment_id}/power/off` | operator | body `{hard: bool}` |
| DELETE | `.../deployments/{deployment_id}/vm` | admin | destructive |
| POST | `/api/callback/{deployment_token}` | single-use token | called by the guest VM, not a user |
| GET/POST | `/api/organizations/{org_id}/webhooks` | admin | credential-bearing, admin only |
| PATCH/DELETE | `.../webhooks/{webhook_id}` | admin | |
| POST | `.../webhooks/{webhook_id}/test` | admin | sends a synthetic event immediately |
| GET | `.../webhooks/{webhook_id}/deliveries` | admin | last 20 |
| GET | `/api/notifications` | authenticated | own notifications, paginated (`limit`/`offset`) |
| GET | `/api/notifications/unread-count` | authenticated | polled by the bell icon |
| POST | `/api/notifications/{id}/read` | authenticated | |
| POST | `/api/notifications/read-all` | authenticated | |
| GET/PUT | `/api/notification-preferences` | authenticated | self only, lazily created with defaults |
| GET/PUT | `/api/organizations/{org_id}/settings[/{key}]` | readonly / admin | |
| GET/PUT | `/api/settings/global[/{key}]` | admin (global) | |
| GET/PUT | `/api/settings/global/m365` | admin (global) | secret is write-only |
| POST | `/api/settings/global/m365/test` | admin (global) | sends a test email to the caller |
| GET | `/api/settings/global/backups` | admin (global) | |
| POST | `/api/settings/global/backups/run` | admin (global) | enqueues an immediate backup |
| GET | `/api/settings/global/backups/{filename}` | admin (global) | downloads a backup file |
| GET | `/api/settings/global/update/status` | admin (global) | current commit, commits behind, live stage if updating |
| POST | `/api/settings/global/update/run` | admin (global) | triggers a self-update; `400` if one is already running |
| GET | `/api/organizations/{org_id}/audit-log` | readonly | paginated (`limit`/`offset`) |
| GET | `/api/dashboard/overview` | admin (global) | |
| GET | `/api/health` | none | `{status: "ok"}` |

## Environment variables

Set in `.env` (loaded by all containers via `env_file`). `scripts/setup.sh`
fills in `APP_SECRET_KEY` and `PROJECT_DIR` for you.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_SECRET_KEY` | yes | none | Fernet key for credential encryption and JWT signing |
| `DATABASE_URL` | yes | none | `postgresql+asyncpg://...` |
| `REDIS_URL` | yes | none | `redis://...`, shared by arq, the login rate limiter, and session tracking |
| `APP_PUBLIC_URL` | no | `http://localhost:8000` | Base URL guest VMs use to reach `/api/callback`, must be reachable from provisioned VMs |
| `ISO_STORAGE_PATH` | no | `/data/isos` | Permanent ISO and logo storage inside the `api`/`worker` containers |
| `ISO_BUILD_TMP` | no | `/data/iso_build_tmp` | Scratch space for answer-file ISO builds and in-progress uploads |
| `BACKUP_DIR` | no | `/data/backups` | Where database backups are written and served from |
| `PROJECT_DIR` | no (self-update only) | none | Absolute host path to this repo, needed only so the `updater` container can resolve volumes/env files correctly when it drives `docker compose` |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | no | `deploycore` / `deploycore` / `deploycore` | Postgres container credentials |

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
automatically. The `worker` and `updater` containers do not hot-reload, run
`docker compose restart worker` (or `updater`) after changing anything they
import.

Tests run against a dedicated `deploycore_test` database that the test suite
creates and tears down itself (`backend/tests/conftest.py`), never against
the real `deploycore` database `make dev` uses, so running `make test`
can't affect real data.

Tests cover: RBAC enforcement (every mutating route rejected below its
floor), autounattend.xml rendering (domain-join present/absent/deferred,
disk layout variants including the recovery partition), deployment state
machine (every legal/illegal transition, retry semantics), Fernet credential
round-trip, ISO-builder temp-directory cleanup on both success and
subprocess failure, deployment/hypervisor org-scoping.

## Known limitations

- Proxmox: registers as a hypervisor type but every driver method raises
  `NotImplementedError`. ESXi is the only working target.
- No VM lifecycle beyond create/power-on/power-off/delete (no snapshots,
  migration, resize, clone-as-VM).
- No LDAP/SSO, local username+password accounts only (with optional TOTP
  2FA).
- Notifications: in-app is always on; email requires configuring Microsoft
  365/Graph yourself, no other provider is supported.
- The post-deploy health check keeps a 30-day history, not indefinite.
- Self-update has no automatic rollback: if a migration or restart fails
  partway through, the containers already running keep running on whatever
  they had, check the Settings page's error message and the container logs.
  It also requires `git pull` to work without a credential prompt (public
  repo, or a credential helper/SSH key already set up on the host) and
  `PROJECT_DIR` to be set correctly; if either isn't true, the feature
  disables itself with a clear message instead of failing silently.
- No tunnel/relay networking, DeployCore assumes it already has a routable
  path to every hypervisor and every hypervisor's guest network.
- Linux provisioning and PXE are out of scope entirely; the whole pipeline
  is Windows-answer-file-ISO specific.
- Audit logging covers most mutating actions but not literally every one
  (e.g. individual ISO chunk uploads aren't logged, only create/finalize).
- Login access tokens can be revoked (logout/logout-all/force-logout), but
  there's no way to list active sessions individually, only revoke all of
  them at once.
