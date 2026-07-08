# Architecture

DeployCore is a multi-tenant web app that provisions Windows Server VMs on customer
hypervisors: create the VM, run a fully unattended OS install from a stock ISO (no
golden images), apply a disk layout, optionally domain-join, install selected roles.
No PXE, no WDS, no WinPE building — provisioning works entirely through a generated
answer-file ISO plus a post-install WinRM session.

## Components

```
┌─────────────┐      ┌──────────────────┐      ┌───────────────────────┐
│  Frontend   │─────▶│  FastAPI backend  │─────▶│  PostgreSQL            │
│  (React SPA)│◀─SSE─│  (API + callback) │      │  (all state)           │
└─────────────┘      └────────┬──────────┘      └───────────────────────┘
                               │ enqueues jobs
                               ▼
                      ┌──────────────────┐      ┌───────────────────────┐
                      │  arq worker       │◀────▶│  Redis                │
                      │  (provisioning    │      │  (job queue, rate     │
                      │   pipeline)       │      │   limit counters)     │
                      └────────┬──────────┘      └───────────────────────┘
                               │
                 ┌─────────────┼─────────────────┐
                 ▼             ▼                 ▼
         Hypervisor API   genisoimage      WinRM (pywinrm)
         (pyvmomi/        (local answer-   (post-install
          proxmoxer)       file ISO build)  config)
```

The backend and worker are separate deployable processes (separate Docker images)
but import the *same* Python package (`backend/app`) for models, hypervisor
drivers, and template rendering — the worker never reimplements domain logic
that already lives in the API layer.

**Network assumption**: the app server is assumed to have direct, routable
network access to each customer's hypervisor management network, and customer
VMs are assumed able to reach the app's public callback endpoint directly
(e.g. because a site-to-site VPN/MPLS link already exists between the MSP and
the customer). This is an infrastructure prerequisite outside the app's scope,
not something DeployCore configures. No tunnel-broker or per-customer relay is
built in this version — see "Non-goals" below for the extension point.

## Data model

All tables use UUID primary keys and `created_at`/`updated_at` timestamps.
Org-scoping follows one consistent pattern: a nullable `org_id` foreign key
means "global" (visible to / inheritable by every organization); a non-null
`org_id` means the row belongs to exactly that organization. This pattern is
used for `DiskLayout`, `DeploymentTemplate`, `IsoAsset`, and `Setting`.

- **Organization** — top-level tenant boundary.
- **User** — global identity with an optional `global_role`, plus a
  `user_org_role` association table for per-organization roles. The
  effective role for a request is `max(global_role, user_org_role[org])`
  under the ordering `admin > operator > readonly > none`.
- **HypervisorHost** — always org-scoped. Holds connection info and an
  encrypted credential blob.
- **DiskLayout** — reusable, JSON-described partition scheme.
- **DeploymentTemplate** — the reusable "recipe": ISO, disk layout,
  hardware sizing, network defaults, locale, encrypted local admin password,
  optional domain-join block, role list, post-install scripts.
- **Deployment** — one provisioning job. Owns two append-only child tables:
  `DeploymentStateTransition` (the state-machine history) and
  `DeploymentLogLine` (the streamed log).
- **IsoAsset** — an uploaded Windows Server or VirtIO driver ISO.
- **AuditLog** — generic `(who, action, target_type/id, detail, when)` event
  row for logins, deployments, template/credential changes.
- **Setting** — hierarchical key/value store resolved
  `deployment → template → org → global`, first match wins.

Every encrypted credential (`HypervisorHost.credential`,
`DeploymentTemplate.local_admin_password`,
`DeploymentTemplate.domain_join_credential`) is stored as a `LargeBinary`
`*_encrypted` column holding Fernet ciphertext keyed by `APP_SECRET_KEY`, and
exposed to Python code only through a property — it is never a column a
`SELECT *`-style query could accidentally serialize. The corresponding
Pydantic **response** schemas omit the plaintext field entirely; it only
appears in `*Create`/`*Update` **request** schemas. Credentials are
write-only by construction, not by convention.

## RBAC

Three roles: `admin > operator > readonly`. Every route — including plain
`GET`s — depends on `require_role(min_role, org_scoped=True)`, which resolves
the caller's effective role for the request's organization and raises `403`
if it's below the floor. There is no endpoint that skips this dependency;
that invariant is what a passing `test_rbac.py` (which introspects the live
route table for every non-`GET` route and asserts a readonly user gets `403`
on all of them) actually proves, rather than relying on per-endpoint tests
staying in sync with new endpoints.

## Hypervisor abstraction

`HypervisorDriver` (`backend/app/hypervisors/base.py`) is an ABC with async
methods for the full VM lifecycle DeployCore needs: `test_connection`,
`create_vm`, `attach_iso`/`detach_iso`, `set_boot_order`, `power_on`/
`power_off`, `get_power_state`, `delete_vm`, and
`upload_iso_to_datastore`/`delete_iso_from_datastore`.

`ESXiDriver` is the fully-implemented driver for this MVP, built on
`pyvmomi` (its calls are synchronous, so they're wrapped in
`asyncio.to_thread`). `ProxmoxDriver` exists as a structural stub — every
method raises `NotImplementedError` — proving the ABC already fits a second
hypervisor before that driver is actually built.

Firmware, SCSI controller, and machine-type defaults are **not** hardcoded
per driver; they live in one data-driven lookup
(`backend/app/hypervisors/defaults.py`):

```python
HYPERVISOR_DEFAULTS = {
    "esxi":    {"firmware": "efi", "scsi_controller": "pvscsi",
                "requires_driver_injection": False},
    "proxmox": {"firmware": "efi", "scsi_controller": "virtio-scsi",
                "requires_driver_injection": True},
}
```

That `requires_driver_injection` flag is the important asymmetry: ESXi's
PVSCSI controller is natively supported by Windows Server 2025, so the
autounattend answer file needs no WinPE-stage driver injection. Proxmox's
VirtIO SCSI controller is **not** natively supported — when the Proxmox
driver is implemented, its VM spec must attach a third (VirtIO driver) ISO
and the answer file must load that driver during the WindowsPE pass, or the
installer will not see the disk at all. Because this lives as one data flag
rather than scattered `if hypervisor_type == "proxmox"` conditionals, adding
the Proxmox driver later is additive, not a rewrite of the pipeline.

## Deployment state machine

```
pending → creating_vm → booting → installing_os → post_install → configuring → completed
   │            │           │            │              │              │
   └────────────┴───────────┴────────────┴──────────────┴──────────────┴──▶ failed
```

`failed` is reachable from every non-terminal state. Transition rules are
enforced by a standalone `DeploymentStateMachine` class
(`backend/app/services/deployment_service.py`) — an explicit allow-list of
`from → {to...}` — rather than inline in the worker task, so the rules are
unit-testable without Redis or arq running.

Responsibility for driving transitions is deliberately split across two
processes, because the callback has to land on an HTTP endpoint (the API
process) while the multi-minute WinRM work has to run somewhere that isn't
blocking a request (the worker process):

1. The arq task `run_deployment` drives `pending → creating_vm → booting`
   directly: it asks the hypervisor driver to build/attach/boot the VM, and
   polls power state.
2. Once the VM is powered on, the guest's `FirstLogonCommands` step (baked
   into the autounattend answer file) POSTs to
   `/api/callback/{deployment.callback_token}`. That FastAPI route validates
   the token is unused, marks it used, and synchronously flips
   `booting → installing_os`.
3. A second arq task, `wait_for_callback`, polls the deployment row for that
   flag (bounded by a configurable timeout, default 90 minutes, resolved
   from `Setting`), and on success runs the WinRM post-install phase itself:
   static IP if needed, hostname rename if needed, `Install-WindowsFeature`
   per configured role (each captured to `DeploymentLogLine`), custom
   PowerShell scripts, domain join here instead of in the answer file if
   `domain_join_timing == "post_install"`, reboot, then a reachability
   check — advancing `post_install → configuring → completed`.

**Retry** is full-retry-from-`pending` (`POST /deployments/{id}/retry`,
operator+). This is safe by construction: DeployCore never reuses a
partially-created VM. The pipeline's own cleanup step deletes any
partially-created VM before a deployment is marked `failed`, so a retry
never collides with stale hypervisor state — it always starts from a clean
slate exactly like a first attempt.

## autounattend.xml rendering

Templates live in `backend/app/templates/xml/` and are composed with Jinja2
`{% include %}`:

- `autounattend_base.xml.j2` — the skeleton: image selection, locale/
  timezone/keyboard, computer name, local admin password.
- `_disk_configuration.xml.j2` — renders `DeployCore`'s `DiskLayout.layout_json`
  into `<DiskConfiguration>`: a fixed EFI partition, an MSR partition, the OS
  volume (either a fixed size or, when `layout_json.os_volume == "remaining"`,
  `<Extend>true</Extend>`), then a loop over any additional labeled volumes.
- `_domain_join.xml.j2` — the `<UnattendedJoin>` block. Included **only**
  when `template.domain_join_enabled and template.domain_join_timing ==
  "answer_file"`. Both "domain join disabled" and "domain join deferred to
  post-install" fall through to *no block at all*, which cleanly produces a
  workgroup machine — there's no separate "no-op" template to maintain.
- `_first_logon_commands.xml.j2` — enables WinRM with a scoped firewall
  rule, then calls back to
  `{{ callback_base_url }}/api/callback/{{ deployment.callback_token }}`.

A single function, `template_render.render_autounattend(deployment)`, is the
only place this composition happens — both the deployment wizard's preview
step and the actual ISO build call it, which is what guarantees the preview
an operator reviews before deploying is byte-identical to what actually
ships on the VM.

**Secondary ISO build** (`backend/app/services/iso_builder.py`):

1. Write the rendered XML plus any bootstrap scripts into a per-deployment
   temp directory named by the deployment's UUID (not a random name), so a
   crashed build is traceable by directory listing.
2. Shell out to `genisoimage -o {output} -J -R {temp_dir}` via
   `asyncio.create_subprocess_exec` — never `shell=True`, since paths
   contain operator-controlled hostnames.
3. Upload the resulting ISO to the hypervisor datastore.

Cleanup is two-tier, because the answer-file ISO contains a plaintext local
admin password and must not survive anywhere longer than necessary:

- The **local temp directory** is removed in a `finally` immediately after
  the build/upload step, regardless of outcome.
- The **datastore-side copy** is removed by the deployment pipeline's own
  outer `finally`, which always calls `delete_iso_from_datastore` if a
  remote path was ever recorded — whether the deployment ultimately reaches
  `completed` or `failed`.

## Background jobs — arq, not Celery

The whole backend is already async (FastAPI + SQLAlchemy 2.x async engine).
arq's job functions are plain `async def` running on the same event loop and
the same Redis instance already required for rate limiting, so there's no
second broker to run and no Celery-style sync-worker-in-a-thread-pool
mismatch to manage. The pipeline's actual bottleneck — waiting on VM boot
and WinRM round-trips — is I/O-bound, which is exactly arq's strength. For a
bare-bones MVP this is meaningfully less operational surface than Celery
(no separate result backend, no worker concurrency model to tune).

Worker tasks (`worker/worker/tasks/provision.py`):

| Task | Responsibility |
|---|---|
| `run_deployment` | Build ISOs, create/attach/boot the VM, drive `pending→creating_vm→booting` |
| `wait_for_callback` | Poll for the callback flag with timeout, then run post-install |
| `run_post_install` | WinRM phase: network, rename, roles, scripts, domain join, reboot, verify |
| `cleanup_deployment` | Always-run finalizer: remote ISO, local temp, partial VM on failure |
| `test_hypervisor_connection` | Backs the UI's "Test Connection" button so it isn't a blocking request |

`maintenance.sweep_stale_deployments` runs on arq's built-in cron to
force-fail deployments stuck past their stage timeout, then invokes the same
`cleanup_deployment` finalizer.

**Progress UI uses Server-Sent Events, deliberately simplified.**
`GET /deployments/{id}/events` streams via FastAPI's native
`StreamingResponse` (`text/event-stream`), polling `DeploymentLogLine` and
`DeploymentStateTransition` directly from Postgres roughly once a second
inside the generator. A more "production" design would have the worker
publish to a Redis pub/sub channel and have the SSE endpoint subscribe
instead of polling — that's a real option later, but it means wiring a
publish call into every place a worker task writes a log line or
transition. A 1-second poll against an already-indexed `(deployment_id, ts)`
table is simpler to build and reason about, and Postgres is already a hard
dependency. Revisit if 1-second latency or DB load from many concurrent
streams ever becomes a real problem.

## Auth

Stateless bearer JWT, not server-side sessions: the frontend is a separate
SPA talking to the API purely over REST, so there's no server-rendered page
to hang a session cookie's lifecycle on. Access tokens are HS256-signed
directly with `APP_SECRET_KEY` (the same key material used for Fernet
credential encryption) and expire after ~12 hours; there is deliberately no
refresh-token table or rotation flow in this MVP — a user simply
re-authenticates after expiry. Reusing one key across two purposes and
skipping refresh-token infrastructure are both intentional MVP
simplifications: the security cost of key reuse at this scale is low, and
re-login-on-expiry is a minor UX cost, not a real gap. Add HKDF-derived key
separation and refresh-token rotation if either ever becomes a genuine
requirement (e.g. an external security review, or session length becoming a
real user complaint).

Passwords are hashed with argon2 (`argon2-cffi`). Auth endpoints
(`/api/auth/login` and equivalent) are rate-limited with a hand-rolled Redis
`INCR`+`EXPIRE` sliding window (10 attempts / 5 minutes per source IP and per
email) — this reuses the Redis instance already required for arq rather than
adding a rate-limiting library for a two-endpoint need.

## First-run setup

There is no seeded admin account and no manual env-var bootstrap step for
production use (`scripts/seed.py` remains for demo/dev data only). Instead,
`GET /api/setup/status` reports whether any `User` row exists at all; while
none does, the SPA shows a two-step setup wizard instead of the login
screen — name the instance (your own MSP's identity, stored as a single
`Setting` at `global` scope under the key `instance_name`) and create the
first account, which is always a global `admin`. `POST /api/setup` performs
both in one transaction and returns an access token so the wizard logs
straight into the app. Once any user exists, both endpoints stop accepting
writes (`409`) — the instance name is edited afterward from Settings by any
global admin, under the "MSP Organization" panel.

Note there is no separate "MSP organization" row in the data model — your
own organization is simply the `instance_name` branding plus whichever users
hold `global_role = admin`; customer organizations are ordinary
`Organization` rows those admins manage, created through the normal
Organizations page.

## VM lifecycle beyond the provisioning pipeline

The provisioning pipeline owns VM creation end-to-end, but a completed
deployment's VM is still a real machine an operator needs to manage
afterward. The deployment detail view exposes power state (polled live from
the hypervisor via `HypervisorDriver.get_power_state`, not cached in
Postgres) and three direct, synchronous API calls — `POST .../power/on`,
`POST .../power/off` (graceful or hard), and `DELETE .../vm` — each calling
the driver in-request rather than through arq, since a single power op is
fast enough not to need the queue. Deleting the VM clears `vm_moref` but
keeps the `Deployment` row and its full history/log for audit purposes; it
does not touch the deployment's `state` (a deployment stays `completed` even
after its VM is later deleted). VM deletion is admin-only since it's
destructive and hard to reverse; power on/off is operator-level.

## Non-goals (explicit extension points, not built now)

- **Linux provisioning, PXE/WDS/WinPE building** — out of scope entirely;
  the answer-file-ISO approach is Windows-specific by design.
- **VM lifecycle beyond create/start/stop/delete** — no snapshots, no
  migration, no resize.
- **Metrics/monitoring integrations, notifications** — not built.
- **LDAP/SSO auth** — the User model and JWT auth layer are structured so
  an external identity provider could be added as an alternative
  `get_current_user` implementation later, but nothing SSO-specific exists
  yet.
- **Tunnel/agent-relay networking** — the hypervisor client and the
  callback endpoint are ordinary HTTP clients/servers with no baked-in
  assumption about *how* the network path exists, so a future per-customer
  relay agent could sit transparently behind both without changing this
  layer's code. Not built in this MVP; the flat/routable network is assumed
  to already exist.
- **Proxmox driver** — the ABC and defaults table already account for it
  (see `requires_driver_injection` above); only the concrete
  `ProxmoxDriver` implementation itself remains.
