# DeployCore

Multi-tenant web application for automated Windows Server provisioning on
customer hypervisors (ESXi implemented, Proxmox stubbed). Each deployment
creates a VM, builds a per-deployment unattended-install ISO, boots and
installs Windows Server from a registered ISO (no golden images — a fresh
install every time), applies a disk layout, optionally joins a domain, and
installs selected Windows roles over WinRM. "DeployCore" is a working title.

Design rationale and internal architecture (state machine, rendering
pipeline, driver abstraction) are in [ARCHITECTURE.md](./ARCHITECTURE.md).
This document covers installation, uninstallation, and a factual inventory
of every capability and API endpoint.

## Requirements

- Docker Engine + Docker Compose v2 (`docker compose`, not `docker-compose`)
- Outbound network access from the host running DeployCore to every
  customer hypervisor's management API, and inbound access from customer
  VMs back to DeployCore's callback endpoint (`APP_PUBLIC_URL`). DeployCore
  does not provide a tunnel or VPN — this connectivity must already exist.
- A Windows Server installation ISO and, for Proxmox targets, a VirtIO
  driver ISO (uploaded through the UI, not bundled)
- ESXi/vCenter host with API access for the hypervisor(s) you intend to
  provision to

## Installation

```bash
git clone <repo-url> deploycore
cd deploycore
cp .env.example .env
```

Edit `.env` and set `APP_SECRET_KEY` (required — used for Fernet credential
encryption and JWT signing). Generate one with:

```bash
python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Also review `APP_PUBLIC_URL` in `.env` — this must be a URL that guest VMs
being provisioned can reach; `http://localhost:8000` only works if
DeployCore and the guest VMs share a network where that resolves, which is
not the case in most real deployments. Set it to the routable
address/hostname of the host running DeployCore.

Start the stack:

```bash
docker compose up --build -d
docker compose exec api alembic upgrade head
```

(equivalently: `make dev` runs the first command in the foreground, `make
migrate` runs the second)

This starts five containers: `postgres` (16), `redis` (7), `api` (FastAPI,
port 8000), `worker` (arq, no exposed port), `frontend` (Vite dev server,
port 5173). Open `http://localhost:5173`.

### First run

A fresh instance has no users. The web UI shows a two-step setup wizard
instead of a login screen: instance name, then the first administrator
account (email, display name, password). Submitting both creates the admin
user and logs in automatically. `POST /api/setup` refuses to run a second
time once any user exists — there is no other bootstrap mechanism and no
default credentials.

After setup, add a hypervisor host, upload a Windows Server ISO, create a
customer organization, and build the rest as documented below.

### Demo data (optional, separate from setup)

```bash
docker compose exec api python scripts/seed.py   # or: make seed
```

Creates an organization (`acme-demo`), three users (`admin@example.com` /
`operator@example.com` / `readonly@example.com`, password `ChangeMe123!`,
one per role), a disk layout, and a workgroup template with the `Web-Server`
role. Does not create an ISO asset or hypervisor host (both need real
binaries/credentials). Refuses to run twice (checks for the `acme-demo`
slug).

## Uninstallation

```bash
docker compose down -v
```

`-v` removes the named volumes (`postgres_data`, `iso_storage`,
`iso_build_tmp`) — this deletes the database and every uploaded ISO.
Omit `-v` to keep that data for a later `docker compose up`. Then remove
the cloned directory. There is no other persistent state: DeployCore writes
nothing outside its containers and named volumes, and modifies nothing on
the Docker host itself.

To remove a single hypervisor's registration without uninstalling anything,
delete it from the Hypervisors page (see below) — this only removes
DeployCore's stored connection/credentials, it does not touch the
hypervisor or any VMs already created on it.

## Roles and multi-tenancy

Three roles, ordered `admin > operator > readonly`. A user has a
`global_role` (applies everywhere) and/or per-organization roles (`Users`
page → assign org role). The effective role for a request is the higher of
the two for that organization. `none` (the default for a newly created
user with no explicit role) grants no access anywhere.

| Role | Can do |
|---|---|
| `readonly` | View everything in organizations they're scoped to |
| `operator` | Everything `readonly` can, plus: create/retry deployments, power on/shut down/power off a deployment's VM, create/edit/delete disk layouts, templates, and ISO assets, clone templates |
| `admin` | Everything `operator` can, plus: create/edit organizations, manage hypervisor hosts (including credentials) and run Test Connection, delete a deployment's VM, edit organization/global settings, manage users and their org-role assignments (global-admin only), rename the MSP instance (global-admin only) |

RBAC is enforced server-side on every route (a dependency resolves the
caller's effective role for the request's organization and returns `403`
below the floor) — the UI hiding a button is a convenience, not the
enforcement point.

Every `Organization` is an independent tenant: its own hypervisors,
templates, disk layouts, ISO assets, deployments, settings, and audit log.
There is no separate "MSP organization" entity — the instance itself is
identified by the `instance_name` (set during setup, editable afterward)
and "MSP admin" means any user with `global_role = admin`, who can see and
manage every organization. `DiskLayout`, `DeploymentTemplate`, and
`IsoAsset` can also be created with no organization (`global`), in which
case every organization inherits them read-only and can clone them into an
org-scoped copy.

## Capabilities

### Setup & instance identity
- One-time setup wizard (instance name + first admin account); locked out
  (`409`) once any user exists
- Instance name shown in the sidebar and sign-in screen; editable afterward
  from Settings → MSP Organization (global-admin only)

### Organizations
- List (scoped to what the caller can see: all orgs for a global-role user,
  else only orgs they have an explicit role in), create, view, edit
  (name/description/active flag) — no delete (deactivate instead)

### Users
- Global admin only: list, create (email/password/display name/global
  role), edit (display name/global role/active flag/password), assign or
  remove a per-organization role
- Argon2 password hashing; JWT bearer tokens, ~12h expiry, no refresh-token
  flow (re-authenticate on expiry)
- Login rate-limited to 10 attempts / 5 minutes per source IP and per email

### Hypervisors
- Per-organization. Type `esxi` (implemented) or `proxmox` (registers, but
  every operation raises "not implemented" — no Proxmox provisioning yet)
- Fields: name, API endpoint, username, credential (write-only — never
  returned by the API after creation), TLS verification toggle, default
  datastore, default network
- Test Connection button: runs a real connection attempt via a background
  worker job, updates and displays last-test status/timestamp/message
- Admin-only for create/edit/delete/test; readonly+ for viewing

### Disk Layouts
- Named, reusable partition schemes, org-scoped or global
- Fields: EFI partition size (MB), MSR partition size (MB), OS volume
  (either a fixed size in MB or "remaining disk space"), an arbitrary list
  of additional volumes (label, drive letter, size in MB)
- Rendered directly into the generated `autounattend.xml`'s
  `<DiskConfiguration>` block
- Create/edit (operator+) for org-scoped layouts; a separate global-create
  endpoint exists for admins but has no dedicated UI form yet

### ISO Assets
- Org-scoped or global. Two kinds: `windows_iso`, `virtio_iso` (the latter
  only matters once Proxmox is implemented)
- Chunked upload from the browser (8 MB chunks over sequential POSTs, then
  a finalize call that assembles the file, computes its SHA-256, and marks
  it `complete`) — built for multi-gigabyte ISOs without loading the whole
  file into memory client- or server-side
- Delete removes both the database row and the file on disk

### Deployment Templates
- Org-scoped or global (global templates are inherited read-only by every
  org and can be cloned into an org-scoped copy)
- Fields: name, Windows ISO (nullable — a template can exist before an ISO
  is attached; the pipeline refuses to deploy from it until one is set),
  disk layout, CPU count, RAM (MB), disk size (GB), network name, VLAN ID,
  locale/timezone/keyboard layout, local administrator password
  (write-only), optional domain join (FQDN, join account, join credential
  [write-only], target OU, and timing — `answer_file` bakes the join into
  the unattended install, `post_install` joins afterward over WinRM), list
  of Windows feature names to install (`Install-WindowsFeature` names, e.g.
  `Web-Server`, `DNS`, `DHCP`), list of post-install PowerShell scripts
  (name + script text, run in order after roles)
- Create/edit (operator+); editing a password/credential field blank leaves
  the stored value unchanged
- Clone: duplicates any visible template (own org's or an inherited global
  one) into a new org-scoped copy named "<name> (copy)", including
  encrypted credentials (copied as ciphertext, not re-entered)
- Preview: renders the exact `autounattend.xml` that would be built for a
  given hostname/network configuration, without creating a deployment —
  used by the deployment wizard's review step and byte-identical to what
  actually ships

### Deployments
State machine (enforced server-side, illegal transitions rejected):

```
pending → creating_vm → booting → installing_os → post_install → configuring → completed
                                                                              ↘
                                                                  failed (from any non-terminal state)
```

- Wizard: select template → select hypervisor → hostname + IP config (DHCP
  or static: address/netmask/gateway/DNS) → autounattend.xml preview →
  deploy
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
- Cleanup: the answer-file ISO (contains a plaintext local admin password)
  is deleted from the hypervisor datastore and from local disk on both
  success and failure; a failed deployment's partially-created VM is
  deleted before the deployment is marked `failed`
- Timeout: a background cron job force-fails any deployment stuck past its
  stage timeout (`os_install_timeout_minutes` setting, default 90) and runs
  the same cleanup
- Detail view: live pipeline-stage visualization, full state-transition
  history with timestamps, streaming log output (Server-Sent Events, ~1s
  poll interval, auto-closes when the deployment reaches a terminal state)
- Retry: full retry from `pending` (any state, any stage) — available once
  a deployment is `failed`; safe because nothing is reused, a fresh VM is
  always created
- VM lifecycle (once a VM exists): live power state (read directly from the
  hypervisor, not cached), power on, shut down (graceful) or power off
  (hard), delete VM (admin-only — deletes the VM on the hypervisor but
  keeps the deployment record and its full history/log for audit; does not
  change the deployment's state)

### Settings
Hierarchical key/value store, four scopes: `global` < `org` < `template` <
`deployment`, resolved most-specific-first (deployment override beats
template override beats org beats global). Only `global` and `org` scopes
have UI/API surface today (`template`/`deployment` scopes exist in the data
model for future use but nothing writes to them yet). Known keys in active
use: `instance_name` (global), `os_install_timeout_minutes` (global or org,
default 90 if unset anywhere).

### Audit Log
Per-organization, append-only. Records action, target type/ID, acting user,
timestamp, and a JSON detail blob. Currently written on: deployment create,
VM power on/off, VM delete, instance setup. Not yet written on: login,
template/hypervisor/user CRUD (the table and API support arbitrary events;
most mutation points don't call it yet).

### Dashboard
- Per-organization: running/completed/failed deployment counts, hypervisor
  connection health, 8 most recent deployments
- Cross-organization overview (global admins only): one row per
  organization with the same counts, click a row to switch the active
  organization

## API reference

All routes are under `/api`. Auth is a JWT bearer token
(`Authorization: Bearer <token>`) except where noted. RBAC floor is the
minimum effective role for the request's organization unless marked
"(global)", meaning the floor applies instance-wide regardless of org.

| Method | Path | Floor | Notes |
|---|---|---|---|
| GET | `/api/setup/status` | none | `{needs_setup: bool}` |
| POST | `/api/setup` | none | one-shot; `409` if already set up |
| GET | `/api/instance` | none | `{name: str}` |
| POST | `/api/auth/login` | none | rate-limited |
| GET | `/api/auth/me` | authenticated | current user + org-role map |
| GET/POST | `/api/organizations` | readonly / admin (global) | |
| GET/PATCH | `/api/organizations/{org_id}` | readonly / admin | |
| GET/POST | `/api/users` | admin (global) | |
| GET/PATCH | `/api/users/{user_id}` | admin (global) | |
| POST/DELETE | `/api/users/{user_id}/org-roles[/{org_id}]` | admin (global) | |
| GET/POST | `/api/organizations/{org_id}/hypervisors` | readonly / admin | |
| GET/PATCH/DELETE | `/api/organizations/{org_id}/hypervisors/{host_id}` | readonly / admin | |
| POST | `.../hypervisors/{host_id}/test-connection` | admin | enqueues a worker job, waits up to 20s |
| GET/POST | `/api/organizations/{org_id}/disk-layouts` | readonly / operator | |
| PATCH/DELETE | `.../disk-layouts/{layout_id}` | operator | org-owned only |
| POST | `/api/disk-layouts/global` | admin (global) | |
| GET/POST | `/api/organizations/{org_id}/iso-assets` | readonly / operator | |
| POST | `.../iso-assets/{iso_id}/chunk` | operator | raw body, one chunk |
| POST | `.../iso-assets/{iso_id}/finalize` | operator | assembles + checksums |
| DELETE | `.../iso-assets/{iso_id}` | operator | |
| GET/POST | `/api/organizations/{org_id}/templates` | readonly / operator | |
| PATCH/DELETE | `.../templates/{template_id}` | operator | org-owned only |
| POST | `.../templates/{template_id}/clone` | operator | any visible template |
| POST | `.../templates/{template_id}/preview` | operator | renders XML, no side effects |
| GET/POST | `/api/organizations/{org_id}/deployments` | readonly / operator | |
| GET | `.../deployments/{deployment_id}` | readonly | |
| GET | `.../deployments/{deployment_id}/history` | readonly | state transitions |
| GET | `.../deployments/{deployment_id}/logs` | readonly | |
| GET | `.../deployments/{deployment_id}/events` | readonly | SSE stream |
| POST | `.../deployments/{deployment_id}/retry` | operator | only from `failed` |
| GET | `.../deployments/{deployment_id}/power` | readonly | live hypervisor query |
| POST | `.../deployments/{deployment_id}/power/on` | operator | |
| POST | `.../deployments/{deployment_id}/power/off` | operator | body `{hard: bool}` |
| DELETE | `.../deployments/{deployment_id}/vm` | admin | destructive |
| POST | `/api/callback/{deployment_token}` | single-use token | called by the guest VM, not a user |
| GET/PUT | `/api/organizations/{org_id}/settings[/{key}]` | readonly / admin | |
| GET/PUT | `/api/settings/global[/{key}]` | admin (global) | |
| GET | `/api/organizations/{org_id}/audit-log` | readonly | last 200 events |
| GET | `/api/dashboard/overview` | admin (global) | |
| GET | `/api/health` | none | `{status: "ok"}` |

## Environment variables

Set in `.env` (loaded by all containers via `env_file`).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_SECRET_KEY` | yes | — | Fernet key for credential encryption and JWT signing (`cryptography.fernet.Fernet.generate_key()` format) |
| `DATABASE_URL` | yes | — | `postgresql+asyncpg://...` |
| `REDIS_URL` | yes | — | `redis://...`, shared by arq and the login rate limiter |
| `APP_PUBLIC_URL` | no | `http://localhost:8000` | Base URL guest VMs use to reach `/api/callback` — must be reachable from provisioned VMs |
| `ISO_STORAGE_PATH` | no | `/data/isos` | Permanent ISO storage inside the `api`/`worker` containers |
| `ISO_BUILD_TMP` | no | `/data/iso_build_tmp` | Scratch space for answer-file ISO builds and in-progress uploads |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | no | `deploycore` / `deploycore` / `deploycore` | Postgres container credentials |

## Development

| Command | Effect |
|---|---|
| `make dev` | `docker compose up --build` (foreground) |
| `make migrate` | Runs Alembic migrations against the running Postgres |
| `make test` | Runs the backend test suite (pytest, against the real Postgres container — no mocked DB) |
| `make seed` | Runs `scripts/seed.py` |
| `make down` | `docker compose down` (keeps volumes) |

Tests: RBAC enforcement (every mutating route rejected below its floor),
autounattend.xml rendering (domain-join present/absent/deferred, disk
layout variants), deployment state machine (every legal/illegal transition,
retry semantics), Fernet credential round-trip, ISO-builder temp-directory
cleanup on both success and subprocess failure, deployment/hypervisor
org-scoping.

## Known limitations

- Proxmox: registers as a hypervisor type but every driver method raises
  `NotImplementedError`. ESXi is the only working target.
- No VM lifecycle beyond create/power-on/power-off/delete (no snapshots,
  migration, resize, clone-as-VM).
- No LDAP/SSO — local email+password accounts only.
- No notifications (email/webhook/etc.) on deployment completion or
  failure — the UI/API must be polled or watched.
- No tunnel/relay networking — DeployCore assumes it already has a routable
  path to every hypervisor and every hypervisor's guest network.
- Linux provisioning and PXE are out of scope entirely; the whole pipeline
  is Windows-answer-file-ISO specific.
- Audit logging covers a subset of mutating actions (see above), not all of
  them.
- Login access tokens are not revocable before expiry (no refresh-token /
  session table) — expiry is the only way a token stops working.
