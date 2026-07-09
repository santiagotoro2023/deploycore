import { ReactNode } from "react";

export function P({ children }: { children: ReactNode }) {
  return <p className="text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">{children}</p>;
}

export function List({ items }: { items: ReactNode[] }) {
  return (
    <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ul>
  );
}

export function Steps({ items }: { items: ReactNode[] }) {
  return (
    <ol className="list-decimal space-y-1.5 pl-5 text-sm leading-relaxed text-neutral-700 dark:text-neutral-300">
      {items.map((item, i) => (
        <li key={i}>{item}</li>
      ))}
    </ol>
  );
}

export function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-neutral-100 px-1 py-0.5 font-mono text-[0.85em] text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200">
      {children}
    </code>
  );
}

export interface WikiArticle {
  id: string;
  title: string;
  overview: ReactNode;
  deepDive: ReactNode;
}

export interface WikiCategory {
  id: string;
  label: string;
  articles: WikiArticle[];
}

export const WIKI_CATEGORIES: WikiCategory[] = [
  {
    id: "getting-started",
    label: "Getting started",
    articles: [
      {
        id: "first-deployment",
        title: "Deploying your first server, end to end",
        overview: (
          <>
            <P>
              A deployment turns a template (the recipe) into a real Windows Server VM on one of your
              hypervisors. Before your first deployment you need four things in place: a hypervisor
              connection, a Windows Server ISO, a disk layout, and a template. Most of this only needs
              doing once per organization.
            </P>
            <P>
              The Dashboard shows a "Getting started" checklist with the three setup steps until they're
              all done, so you always know what's missing.
            </P>
          </>
        ),
        deepDive: (
          <>
            <Steps
              items={[
                <>
                  <strong>Add a hypervisor.</strong> Hypervisors page → New hypervisor. Enter the ESXi
                  endpoint and credentials, then click <strong>Test Connection</strong> before saving, so
                  a typo or bad password shows up immediately instead of mid-deployment.
                </>,
                <>
                  <strong>Upload a Windows Server ISO.</strong> ISO Assets page. Uploads happen in 8&nbsp;MB
                  chunks, so this works fine over a slow link, just keep the tab open until it finishes.
                </>,
                <>
                  <strong>Check the disk layout.</strong> Disk Layouts page. A sensible default (EFI + MSR +
                  a recovery partition + the OS volume taking the rest) is created automatically during
                  setup; most people never need to touch this.
                </>,
                <>
                  <strong>Create a template.</strong> Templates page. Pick the ISO, the disk layout,
                  CPU/RAM/disk size, the network's port group name, an admin password, optional domain
                  join, and which Windows roles to install.
                </>,
                <>
                  <strong>Deploy.</strong> Deployments page → New deployment. Pick the template and
                  hypervisor, give it a hostname and IP configuration, review the exact answer file that
                  will be used, and deploy.
                </>,
              ]}
            />
            <P>
              Once deployed, watch it live on the deployment's detail page: VM creation, Windows Setup,
              role installation, everything streams in as it happens. If a step fails, the same page has a
              <strong> Download full log</strong> button, one text file with every stage, every command,
              and the full error/traceback, the fastest way to see what actually went wrong.
            </P>
            <P>
              Need more than one server the same way? Toggle <strong>Bulk deployment</strong> in the
              wizard: give it a hostname prefix and a count (1–50) instead of a single hostname, and it
              creates that many deployments in one go (<Code>PREFIX01</Code>, <Code>PREFIX02</Code>, ...).
              Bulk deployments are DHCP only, there's no per-VM static IP allocation in bulk mode.
            </P>
          </>
        ),
      },
      {
        id: "organizations",
        title: "Organizations and multi-tenancy",
        overview: (
          <>
            <P>
              An organization is a customer environment: its own hypervisors, ISOs, disk layouts,
              templates, deployments, webhooks, settings, and audit log, fully separate from every other
              organization in the same instance. This is what lets one DeployCore instance run several
              customers side by side without their data or credentials ever mixing.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              There's no separate "MSP organization" entity in the data model. The instance itself is
              identified by its <Code>instance_name</Code> (set during setup, editable later from Settings),
              and "MSP admin" just means any user whose global role is <Code>admin</Code>, they can see and
              manage every organization that exists.
            </P>
            <List
              items={[
                <>Organizations page: list (scoped to what the caller can see), create, view, edit
                  name/description/active flag.</>,
                <><Code>DiskLayout</Code>, <Code>DeploymentTemplate</Code>, and <Code>IsoAsset</Code> can
                  also be created with no organization at all (global scope), in which case every
                  organization inherits them read-only and can clone one into its own org-scoped copy.</>,
              ]}
            />
          </>
        ),
      },
      {
        id: "deleting-an-organization",
        title: "Deleting an organization",
        overview: (
          <>
            <P>
              Global admins can permanently delete an organization from the Organizations page. This
              removes absolutely everything that belongs to it, not just the organization record itself,
              there's no undo.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>Deleting an organization removes, in one action:</P>
            <List
              items={[
                "Its hypervisor connections and stored credentials",
                "Its disk layouts, templates, and ISO assets (the ISO files are removed from disk too)",
                "Its deployment records and full history/logs",
                "Its webhooks",
                "Its organization-scoped settings (like the deployment timeout override)",
                "Every user's role assignment for that specific organization",
              ]}
            />
            <P>
              Two things this does <strong>not</strong> do: it doesn't touch any VM already created on the
              organization's hypervisors, DeployCore simply loses its own record of how to reach that
              hypervisor, the VM keeps running exactly as it was until someone removes it directly on the
              hypervisor. It also doesn't erase the organization from the audit log, those entries survive
              with their organization reference cleared, so there's still a permanent record that the
              deletion happened, who did it, and when.
            </P>
            <P>
              Only a global admin can do this (the same floor as creating an organization in the first
              place), and the confirmation dialog spells out everything above again before it lets you
              proceed.
            </P>
          </>
        ),
      },
    ],
  },
  {
    id: "access-security",
    label: "Access & security",
    articles: [
      {
        id: "users-roles",
        title: "Users, roles, and permissions",
        overview: (
          <>
            <P>
              Every user has three possible role levels, from lowest to highest:{" "}
              <Code>readonly</Code> → <Code>operator</Code> → <Code>admin</Code>. A role can be granted
              globally (applies everywhere) or per organization, and DeployCore always uses whichever is
              higher for the organization you're currently working in.
            </P>
          </>
        ),
        deepDive: (
          <>
            <List
              items={[
                <><Code>readonly</Code>: view everything in the organizations they're scoped to.</>,
                <><Code>operator</Code>: everything readonly can, plus create/retry/bulk-create
                  deployments, power a deployment's VM on/off, and create/edit/delete disk layouts,
                  templates, and ISO assets, including clone/export/import.</>,
                <><Code>admin</Code>: everything operator can, plus manage organizations, hypervisor
                  hosts, and webhooks (including credentials), delete a deployment record, edit settings,
                  and (global admin only) manage users (including deactivating/reactivating or
                  permanently deleting one) and their org-role assignments, instance branding, M365
                  email, backups, and self-update.</>,
              ]}
            />
            <P>
              A brand-new user with no role assignment at all has the implicit <Code>none</Code> role and
              can't access anything until an admin assigns one. The Users page's Access column is where
              this happens: the "Assign..." dropdown next to a user's existing roles offers every
              organization they don't already have a role in, plus a <strong>Global (all organizations)</strong>{" "}
              option at the top for granting (or changing) their global role the same way, no need to open
              a separate edit form just to make someone a global admin. A user's global access, when they
              have one, shows as its own removable "Global: role" badge right there alongside their
              per-organization ones, so it's never mistaken for "no access" just because the organization
              list happens to be empty. RBAC is enforced on the server for every route, the UI hiding a
              button you can't use is a convenience, not the actual security boundary.
            </P>
            <P>
              Users sign in by <strong>username</strong>, not email. Email is entirely optional and only
              used for M365 notification delivery if you configure that (see "Email notifications").
            </P>
            <P>
              <strong>Deactivate</strong> (global admin only) blocks sign-in immediately without deleting
              anything, everything about the account, its role assignments, its history, is kept exactly
              as-is; <strong>Activate</strong> reverses it, same account, nothing to redo. Use this for
              someone leaving temporarily, or while you sort out whether they should keep access at all.{" "}
              <strong>Delete</strong> is permanent: the account and its org-role assignments are gone for
              good, though any deployment they created is kept, just no longer attributed to anyone. You
              can't delete your own account this way, only another admin can.
            </P>
          </>
        ),
      },
      {
        id: "two-factor",
        title: "Two-factor authentication",
        overview: (
          <>
            <P>
              Any user can turn on an extra login step from the Account page: a 6-digit code from an
              authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, etc.), in
              addition to their password.
            </P>
          </>
        ),
        deepDive: (
          <>
            <Steps
              items={[
                <>Account page → Two-factor authentication → <strong>Set up 2FA</strong>.</>,
                <>Scan the QR code that appears with your authenticator app. Can't scan it? Expand
                  "Enter this key manually" underneath the QR code for the same secret as text.</>,
                <>Enter the 6-digit code the app now shows you to confirm, this proves the app is actually
                  synced before 2FA becomes mandatory on your account.</>,
              ]}
            />
            <P>
              Once enabled, every future login asks for username + password first, then a second screen
              for the current code. To turn it off, go back to the same panel and enter a current code to
              disable it, there's no way to remove 2FA from your own account without one (an admin can
              still reset your password, but 2FA itself is self-service only today).
            </P>
          </>
        ),
      },
      {
        id: "sessions",
        title: "Sessions and signing out remotely",
        overview: (
          <>
            <P>
              Logins are tracked server-side, not just a stateless token, so they can be revoked
              immediately: by yourself ("sign out everywhere") or by an admin acting on someone else's
              account ("force logout").
            </P>
          </>
        ),
        deepDive: (
          <>
            <List
              items={[
                <>Account page → <strong>Sign out everywhere</strong>: invalidates every active login for
                  your own account, including the one you're currently using.</>,
                <>Users page → <strong>Force logout</strong> on any user (global admin only): immediately
                  invalidates every active session for that account, useful right after a password reset
                  or when someone leaves.</>,
                <>Login is also rate-limited (10 attempts / 5 minutes, tracked per source IP and per
                  username) to slow down password guessing.</>,
              ]}
            />
            <P>
              There's currently no way to list and revoke individual sessions one at a time, both actions
              above revoke <em>all</em> of them at once.
            </P>
          </>
        ),
      },
    ],
  },
  {
    id: "provisioning",
    label: "Provisioning",
    articles: [
      {
        id: "hypervisors",
        title: "Hypervisors",
        overview: (
          <>
            <P>
              A hypervisor connection tells DeployCore where to actually create VMs. Each organization
              manages its own list, an ESXi host or vCenter endpoint, a service account, and a default
              datastore to use.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>ESXi is the only supported target today. Fields when adding one:</P>
            <List
              items={[
                "Name (label shown throughout the UI)",
                "API endpoint (the ESXi/vCenter host address)",
                "Username and credential (the credential is write-only, it's never returned by the API again once saved)",
                "TLS verification toggle (turn off only for a self-signed lab host you trust)",
                "Default datastore, used when creating a VM if nothing more specific is set",
              ]}
            />
            <P>
              There's no network setting here: the port group/vSwitch a VM's NIC attaches to is defined
              per-template instead (see "Templates and Windows roles"), since different templates on the
              same hypervisor connection can reasonably need different networks.
            </P>
            <P>
              Use <strong>Test Connection</strong> two ways: against whatever is currently typed into the
              create form, before you've saved anything, or against an already-saved host, which stores
              and displays its last result (status, timestamp, message) so you can see connection health
              at a glance later. Deleting a hypervisor only removes DeployCore's stored connection and
              credentials, it never touches the hypervisor itself or any VMs already created on it.
            </P>
          </>
        ),
      },
      {
        id: "iso-assets",
        title: "ISO assets",
        overview: (
          <>
            <P>
              Upload the Windows Server installation media once per organization (or once globally, to
              share across every organization), then reuse it across as many templates as you like.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              Uploads happen from the browser in 8&nbsp;MB chunks over sequential requests, then a
              finalize call assembles the file and computes its SHA-256 checksum. This is deliberate:
              multi-gigabyte ISOs upload reliably without ever holding the whole file in memory on either
              end, and a flaky connection just means a slower upload, not a failed one. Deleting an ISO
              asset removes both the database record and the file on disk immediately.
            </P>
            <P>
              A global admin uploading an ISO gets an extra "Available to" choice in the upload dialog:
              this organization only, or every organization. A global ISO shows up (read-only, as
              "Global" in the Scope column) in every organization's list and can be attached to any
              organization's templates, handy for a Windows ISO every customer environment should share
              instead of re-uploading the same multi-gigabyte file once per organization.
            </P>
          </>
        ),
      },
      {
        id: "disk-layouts",
        title: "Disk layouts",
        overview: (
          <>
            <P>
              A disk layout is a named, reusable partitioning scheme, how the new VM's disk gets split
              into EFI, MSR, an optional recovery partition, and the OS volume. Most organizations never
              need more than the one created automatically during setup.
            </P>
          </>
        ),
        deepDive: (
          <>
            <List
              items={[
                "EFI partition size (MB) and MSR partition size (MB)",
                "Optional recovery partition size (MB), placed between the MSR and OS partitions using the WinRE/recovery GPT partition type, if you want the standard Windows recovery environment available on the disk",
                'OS volume: either a fixed size in MB, or "remaining disk space"',
                "Any number of additional volumes (label, drive letter, size in MB)",
              ]}
            />
            <P>
              The layout created automatically during setup uses EFI 100&nbsp;MB, MSR 16&nbsp;MB, a
              1000&nbsp;MB recovery partition, and the OS volume taking whatever's left, this is rendered
              directly into the generated <Code>autounattend.xml</Code>'s <Code>&lt;DiskConfiguration&gt;</Code> block
              at deploy time. Layouts can be exported to a JSON file and imported again (as a new org-scoped
              copy), handy for keeping a layout consistent across organizations or instances.
            </P>
          </>
        ),
      },
      {
        id: "templates",
        title: "Templates and Windows roles",
        overview: (
          <>
            <P>
              A template is the reusable recipe for a server: which ISO, which disk layout,
              CPU/RAM/disk size, networking, credentials, optional domain join, and which Windows roles
              to install. Every deployment is created from exactly one template.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>Full field list:</P>
            <List
              items={[
                "Name, and the Windows ISO to use (a template can exist before an ISO is attached, but it can't deploy until one is set)",
                <>Disk layout, CPU count and cores per socket, RAM (MB), disk size (GB) and its{" "}
                  <strong>provisioning type</strong>: thin (space allocated on demand), thick lazily zeroed
                  (space reserved up front, zeroed on first write), or thick eagerly zeroed (space reserved
                  and zeroed entirely at creation time, slower to create but avoids any first-write
                  latency, the option most production databases and similar disk-latency-sensitive
                  workloads want).</>,
                <>Network name, this is the ESXi/vCenter <strong>port group or vSwitch name</strong> exactly
                  as it appears in your hypervisor's networking configuration, not a Windows-side network
                  name, it's what the new VM's virtual NIC attaches to. Also its{" "}
                  <strong>adapter type</strong> (VMXNET3, the paravirtualized default with the best
                  performance and the one to use unless you have a specific reason not to; E1000/E1000E
                  emulate real Intel NICs, only needed for guest OS or driver compatibility). Also VLAN ID,
                  locale, timezone, and keyboard layout.</>,
                "Local administrator password (write-only, never shown again after saving)",
                <>Optional domain join: FQDN, join account, join credential (write-only), target OU, and
                  timing, either <Code>answer_file</Code> (baked into the unattended install) or{" "}
                  <Code>post_install</Code> (joined afterward over WinRM).</>,
                "A curated list of Windows roles/features, picked as checkboxes: AD Domain Services, DNS, DHCP, Web Server (IIS), Print Services, Remote Desktop Session Host, DFS Namespaces, DFS Replication.",
                "Any number of post-install PowerShell scripts (name + script text), run in order after roles are installed.",
              ]}
            />
            <P>
              <strong>Important limitation:</strong> each role is installed with a plain{" "}
              <Code>Install-WindowsFeature</Code>, nothing more. Checking "Active Directory Domain
              Services" installs the AD DS role binaries only, it does <strong>not</strong> promote the
              server to a domain controller and does <strong>not</strong> create or configure any Group
              Policy Objects, OUs, or delegation. If you actually need a domain controller, you still need
              to run <Code>Install-ADDSForest</Code> (or the classic <Code>dcpromo</Code> flow) and set up
              GPOs through GPMC yourself afterward, nothing here is automated.
            </P>
            <P>
              Other things a template can do: <strong>Clone</strong> duplicates any visible template (your
              own, or an inherited global one) into a new org-scoped copy, including its encrypted
              credentials. <strong>Export</strong>/<strong>Import</strong> round-trip a template as JSON
              (credentials excluded from export; import sets a random placeholder admin password you must
              replace before it can deploy). <strong>Preview</strong> renders the exact{" "}
              <Code>autounattend.xml</Code> that a given hostname/network configuration would produce,
              without creating a deployment, byte-identical to what actually ships. For a deployment that
              already exists, its own detail page has a <strong>View answer file</strong> section instead,
              the exact XML that one actually shipped with, stored once rendered rather than re-derived,
              so it stays correct even if the template it came from is edited afterward.
            </P>
          </>
        ),
      },
      {
        id: "deployments",
        title: "Deployments",
        overview: (
          <>
            <P>
              A deployment is one real Windows Server VM being built from a template: created on the
              hypervisor, installed fresh via Windows Setup (not a cloned image), then configured with
              its roles and scripts. Every deployment goes through the same tracked pipeline, so you can
              always see exactly what stage it's at and what happened if it failed.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>State machine (enforced server-side, illegal transitions are rejected):</P>
            <P>
              <Code>pending → creating_vm → booting → installing_os → post_install → configuring → completed</Code>,
              with <Code>failed</Code> reachable from any non-terminal state.
            </P>
            <List
              items={[
                <>The pipeline runs in the background worker, not the request that created it: it renders
                  the answer file, builds a per-deployment answer-file floppy image, uploads the Windows
                  ISO and the floppy to the hypervisor datastore, creates the VM (UEFI firmware, PVSCSI
                  controller), attaches media, and powers on. The Windows ISO itself is only ever uploaded
                  to a given hypervisor's datastore once per ISO asset, not once per deployment: a second
                  deployment from the same template (or a bulk deployment creating many at once) reuses
                  the copy already there instead of re-transferring a multi-gigabyte file every time.</>,
                <>A Windows install ISO is also rewritten once, the moment it finishes uploading into
                  DeployCore (not per deployment), to remove its "Press any key to boot from CD or
                  DVD..." prompt: every stock Windows ISO ships two UEFI boot images, the normal one that
                  prints that prompt and waits forever for a keystroke, and a second, silent one
                  (<Code>efisys_noprompt.bin</Code>) built by Microsoft for exactly this kind of
                  unattended deployment. DeployCore finds both inside the ISO and swaps the silent one
                  into the slot the boot catalog actually points at (via <Code>xorriso</Code>, rewriting
                  only that one boot image and nothing else), so every VM built from that ISO from then on
                  just boots straight into Setup with nobody watching. If a particular ISO turns out not
                  to have that second boot image (non-standard media), the swap is skipped and the ISO is
                  left untouched rather than failing the upload.</>,
                <>The answer file ships on a floppy image, not a second CD-ROM: a second CD-ROM works
                  for most of the answer file (Windows Setup does find and apply it, disk partitioning,
                  install, domain join, admin password, all unattended), but empirically not reliably for
                  the very first implicit check, the one deciding whether to show the interactive
                  language/time/keyboard screen, which runs before Setup's driver stack is fully up. A
                  floppy is both checked earlier and higher-precedence there (Microsoft's own documented
                  search order), which is more reliable for that one specific check.</>,
                <>With the boot-prompt swap above in place, most deployments never need it, but DeployCore
                  still sends a synthetic Enter keypress to the VM every second for about 15 seconds right
                  after power-on as a safety net (for an ISO the swap skipped, or any other firmware
                  prompt), long enough to bracket when a "press any key" prompt would appear, short enough
                  to stop before Setup's GUI is up and blind Enters would start landing on it instead. The
                  VM's boot order also auto-retries on its own (ESXi's <Code>bootRetryEnabled</Code>) if
                  the whole boot sequence fails, since a freshly attached CD-ROM isn't always connected the
                  instant the VM powers on.</>,
                <>The answer file sets locale/keyboard in two separate, differently-scoped places:
                  <Code>Microsoft-Windows-International-Core-WinPE</Code> in the windowsPE pass (covers
                  only Setup's own UI while it's running) and <Code>Microsoft-Windows-International-Core</Code>{" "}
                  in the specialize pass (covers the actually-installed OS, the built-in Administrator and
                  every new user). Only the first one existed originally, which meant even a perfectly
                  applied answer file could leave the deployed machine itself on a default keyboard;
                  DeployCore now sets both. Keyboard layout is expressed as an explicit
                  <Code>LCID:KLID</Code> hex pair rather than a bare locale tag (e.g. <Code>0807:00000807</Code>{" "}
                  for German (Switzerland)) since a bare tag only picks *a* default keyboard for that
                  locale, not necessarily the one named after it; DeployCore resolves this automatically
                  from the locale you enter for every keyboard layout it knows about (see the Templates
                  page's field hint for the full list, or supply an explicit pair yourself for anything
                  else). Windows Setup's own windowsPE-stage screen has a long-standing, still-open
                  upstream quirk (reported against multiple Windows versions) where a small set of locales
                  don't get their <Code>InputLocale</Code> honored on that one specific screen even though
                  every other part of the answer file, including the specialize-pass value above, applies
                  correctly; DeployCore's second, sparser round of synthetic Enter keypresses (one every 8
                  seconds for about two minutes after the boot prompt, timed for whenever Setup's GUI
                  actually finishes loading) exists to get past that screen automatically if it shows up,
                  without changing what either component sets.</>,
                <>The guest calls back to DeployCore once Windows Setup finishes (a single-use token per
                  deployment), which is what advances the state from booting to installing_os.</>,
                <>Post-install runs over WinRM once the guest reports an IP: apply static network config
                  if requested, install each selected Windows role, run post-install scripts in order,
                  join the domain here if configured for that timing, reboot, verify it comes back
                  reachable, then mark the deployment completed.</>,
                <>A stuck deployment (past its configured timeout, default 90 minutes, editable per
                  organization in Settings) is force-failed automatically by a background job, and cleaned
                  up the same way a real failure would be.</>,
                <>A completed deployment gets a health check every 15 minutes (a WinRM ping to its guest
                  IP), with up to 30 days of history shown as a strip of badges on its detail page. Going
                  from healthy to unreachable also fires a notification/webhook.</>,
              ]}
            />
            <P>
              From the deployment's detail page you can retry a failed deployment from scratch (a fresh VM
              is always created, nothing is reused, so this is always safe), or power the VM on/shut it
              down gracefully/power it off hard. There's no dedicated "delete just the VM" action, remove
              it directly on the hypervisor if you want it gone without deleting the deployment record
              too. The <strong>Download full log</strong> button produces one plain-text file with the
              deployment's details, full state history, and every log line, the fastest way to hand off a
              failure to someone else or attach to a support ticket.
            </P>
            <P>
              <strong>Delete deployment</strong> (admin only) is for cleaning up ones you don't need in
              the list anymore, a pile of old failed attempts, one that's stuck mid-pipeline, say.
              Available at any stage, not just terminal ones. Deleting it doesn't touch the hypervisor at
              all: if a VM still exists for it, that VM keeps running exactly as before, DeployCore just
              stops tracking it, it's no longer reachable through this deployment (use <strong>Delete
              VM</strong> first, a separate, already-destructive action, if you want that gone too), and
              if the pipeline is still actively running in the background worker, deleting the deployment
              doesn't cancel it, that keeps going too, just with nothing in the UI showing it anymore.
              It's a soft delete: the deployment disappears from the list and dashboard counts and its
              own detail page stops resolving, but the row, its state history, and its log lines are not
              actually erased, both the <Code>/history</Code> and <Code>/logs</Code> API endpoints keep
              working for its id afterward. There's currently no UI to browse deleted deployments, this
              is meant as a safety margin (and an audit trail via the <Code>deployment.delete</Code>{" "}
              action, which records whether a VM was left running), not an undo button.
            </P>
          </>
        ),
      },
      {
        id: "troubleshooting-deployments",
        title: "Troubleshooting a failed or stuck deployment",
        overview: (
          <>
            <P>
              Almost every failure traces back to one of a handful of causes: a template missing an ISO,
              a network the VM can't actually reach DeployCore on, or a timeout on a slow install. The
              deployment's own detail page has everything needed to tell which.
            </P>
          </>
        ),
        deepDive: (
          <>
            <List
              items={[
                <><strong>Start with the log stream</strong> on the deployment's detail page: it streams
                  live while a deployment is running, and stays there afterward. The current-step marker
                  (shown at the top) states exactly which pipeline step was running when things stopped
                  progressing or failed, rendering XML, uploading an ISO, creating the VM, each post-install
                  action are all tracked individually.</>,
                <><strong>Download full log</strong> on that same page bundles the deployment's details,
                  full state-transition history, and every log line into one plain-text file, the fastest
                  way to hand a failure off to someone else or attach to a ticket.</>,
                <><strong>Stuck in <Code>booting</Code> or <Code>installing_os</Code> for a long time?</strong>{" "}
                  The guest calls back to DeployCore once Windows Setup finishes; if that callback can
                  never arrive (<Code>APP_PUBLIC_URL</Code> isn't reachable from the VM's network) the
                  deployment will sit there until its timeout trips. Confirm <Code>APP_PUBLIC_URL</Code>{" "}
                  really is reachable from the organization's hypervisor network, not just from your own
                  machine.</>,
                <><strong>Fails immediately with "template has no Windows ISO configured"?</strong> The
                  template was created (or exported/imported) before an ISO was attached to it, attach one
                  on the Templates page.</>,
                <><strong>It ran long, then force-failed on its own?</strong> That's the deployment timeout
                  (Settings → deployment timeout, default 90 minutes) doing its job, not a bug, something
                  legitimately took longer than expected. Raise the timeout for that organization if slow
                  installs are normal in your environment (a slow datastore, for example).</>,
                <><strong>Windows Setup ran but the install looked interactive, not unattended?</strong>{" "}
                  Something in the answer file is off for that particular template/deployment (an
                  unusual character in a password, a role name, ...). <strong>View answer file</strong>{" "}
                  on the deployment's detail page shows the exact XML that shipped, compare it against
                  what you expected.</>,
                <><strong>Once you've found the cause</strong>, fix it (attach an ISO, correct the network
                  name, whatever it was) and use <strong>Retry</strong> on the deployment's detail page: it
                  always creates a completely fresh VM from <Code>pending</Code>, nothing from the failed
                  attempt is reused, so retrying is always safe.</>,
              ]}
            />
          </>
        ),
      },
    ],
  },
  {
    id: "integrations-ops",
    label: "Integrations & operations",
    articles: [
      {
        id: "in-app-notifications",
        title: "In-app notifications",
        overview: (
          <>
            <P>
              The bell icon in the header is on for every user automatically, no configuration needed:
              it's how you find out a deployment you started has moved, without needing email set up at
              all.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              You're notified when a deployment <em>you</em> created starts, completes, or fails, not
              other people's deployments. The bell polls for an unread count every 20 seconds; opening it
              shows your recent notifications, and clicking one marks it read and takes you straight to
              that deployment's detail page. <strong>Mark all read</strong> clears the unread count
              without removing anything; <strong>Clear all</strong> deletes every notification in the
              list outright. This is always on and has no per-user settings, unlike email delivery below,
              which is opt-in per event type.
            </P>
          </>
        ),
      },
      {
        id: "email-notifications",
        title: "Email notifications via Microsoft 365",
        overview: (
          <>
            <P>
              DeployCore can also email users when their deployments start, finish, fail, or go
              unreachable, by sending through your own Microsoft 365 tenant. It's configured once,
              instance-wide, by a global admin, then each user chooses which of those events they
              personally want emailed to them.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              Settings → Email notifications needs an Entra ID (Azure AD) app registration with the{" "}
              <Code>Mail.Send</Code> application permission granted, plus:
            </P>
            <List
              items={[
                "Tenant ID and the app registration's client ID",
                "Client secret (write-only, leave blank on later edits to keep the current one)",
                "The sender mailbox (a UPN that mailbox actually exists and the app has permission to send as)",
              ]}
            />
            <P>
              Use <strong>Send test email</strong> after saving to confirm the whole chain actually works
              before relying on it. Each user then controls their own delivery preferences from the
              Account page (deployment started/completed/failed/became unreachable, complete and failed
              are on by default, start and health checks are off by default to avoid inbox noise). Email
              always sends through a background job, never inline in a request, so a slow or failing mail
              server can never affect deployment outcome or page load time.
            </P>
          </>
        ),
      },
      {
        id: "webhooks",
        title: "Webhooks",
        overview: (
          <>
            <P>
              Webhooks push deployment events to your own ticketing or automation tool (Jira Automation,
              ServiceNow, Zapier, n8n, or anything with an inbound-webhook trigger), instead of DeployCore
              integrating any one of them natively.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              Configure a URL, a signing secret, and which events to send (deployment
              started/completed/failed/retried, or a completed deployment going unreachable). Every
              delivery is POSTed as JSON (<Code>{"{event, occurred_at, data}"}</Code>) with an{" "}
              <Code>X-DeployCore-Signature: sha256=&lt;hmac&gt;</Code> header, an HMAC-SHA256 over the raw
              body using your secret, the same convention GitHub and Stripe use, so most tools' built-in
              "verify signature" step works without modification.
            </P>
            <P>
              Deliveries retry up to 3 times with exponential backoff on failure. Use the{" "}
              <strong>Test</strong> button to send a synthetic event immediately and see the result inline,
              and expand "Deliveries" on any webhook to see its last 20 attempts (status code, success/fail,
              response snippet, timestamp).
            </P>
          </>
        ),
      },
      {
        id: "backups",
        title: "Database backups",
        overview: (
          <>
            <P>
              DeployCore backs up its own database automatically every day, and you can trigger an extra
              one manually at any time from Settings.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              Backups are a compressed <Code>pg_dump</Code> (custom format), run by a background cron job.
              The newest 14 are kept, older ones are pruned automatically as new ones are made. Settings →
              Database backups lists them with size and timestamp, <strong>Run backup now</strong> triggers
              an extra one immediately, and each one can be downloaded directly as a file from the same
              page, useful before a risky change or just to keep an off-instance copy.
            </P>
          </>
        ),
      },
      {
        id: "https-certificate",
        title: "HTTPS certificate",
        overview: (
          <>
            <P>
              DeployCore is served over HTTPS by a built-in reverse proxy, with a self-signed
              certificate generated automatically so it works out of the box. Settings → HTTPS
              certificate lets a global admin upload a real one, or switch back to the self-signed
              one at any time.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              A small <Code>proxy</Code> service (Caddy) sits in front of the rest of the stack: it
              terminates TLS on port 443 and redirects any plain HTTP request on port 80 to it, then
              forwards everything else to the frontend unchanged. Until you upload a certificate of
              your own, it uses <Code>tls internal</Code> with Caddy's <Code>on_demand</Code> option,
              which issues a locally-trusted, self-signed certificate on the fly for whatever
              hostname or IP the connection actually came in on (a plain <Code>tls internal</Code>{" "}
              without that option only ever issues one static certificate for{" "}
              <Code>localhost</Code>/<Code>127.0.0.1</Code>, so a LAN IP, a port-forwarded public IP,
              or any other hostname would get no matching certificate and a hard TLS handshake
              failure instead of a normal untrusted-certificate warning). Browsers still flag the
              connection as untrusted either way, nothing vouches for a certificate you generated
              yourself, that's expected. This default starts immediately on boot and never depends
              on the database being reachable, only switching to an uploaded certificate does.
            </P>
            <P>
              To get rid of that warning, upload a certificate and its matching private key (PEM
              format, an unencrypted key, no passphrase) signed by a CA browsers already trust, e.g.
              one from Let's Encrypt or an internal CA your organization's devices already trust. The
              upload is validated before anything changes: the certificate and key have to actually
              match, and the certificate can't already be expired. Once accepted, the proxy picks it
              up within a few seconds on its own, no restart needed.
            </P>
            <P>
              <strong>Switch to self-signed temporarily</strong> flips back to the generated
              certificate without discarding the uploaded one, useful if the uploaded certificate
              expired or you're troubleshooting something unrelated. <strong>Use this certificate
              again</strong> switches back, no re-upload required. Uploading a new certificate always
              switches to it immediately.
            </P>
            <P>
              Mechanically, the switching (not the self-signed default itself) works the same way
              self-update below does: the API writes a <Code>tls_mode</Code> setting straight to
              Postgres, and the <Code>proxy</Code> container polls that same table on an interval and
              reloads Caddy's config when it no longer matches what's currently serving. Neither
              container talks to the other directly.
            </P>
          </>
        ),
      },
      {
        id: "self-update",
        title: "Self-update",
        overview: (
          <>
            <P>
              Settings → Updates shows whether you're behind the latest code and lets a global admin
              update the running instance with one click, no terminal access needed.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              The updater checks GitHub for new commits on its own every 5 minutes and that's what
              "commit(s) behind" reflects; <strong>Check for update</strong> forces that check
              immediately instead of waiting, useful right after you know something's been pushed.
              It only looks, nothing is pulled or rebuilt until you click Update now separately.
            </P>
            <P>
              Clicking <strong>Update now</strong> pulls the latest code from GitHub, rebuilds, runs any
              new database migrations, and restarts, with a live staged progress indicator (Pulling →
              Building → Restarting → Done). The app is only unreachable for the last part of that,
              usually well under a minute, and nothing in your database is touched by the update process
              itself beyond the migrations it's supposed to run.
            </P>
            <P>
              This works because a small dedicated <Code>updater</Code> container in the stack has access
              to the Docker socket, the same mechanism tools like Portainer or Watchtower use, so it can
              tell the host's Docker daemon what to do. That is a real privilege: that container can, in
              principle, control anything else running on the same Docker host. It's a reasonable
              trade-off for a self-hosted tool where you already control the host, worth knowing before
              you rely on it. Nothing needs configuring for it to find this repo on the host either: it
              asks the Docker API for its own bind mount's source path itself, so it works the same on a
              brand-new install and on an instance that's been running since before this feature existed.
            </P>
            <P>
              If this isn't a git checkout, or that automatic lookup can't work in your particular Docker
              setup (a socket proxy that blocks <Code>docker inspect</Code>, for example), the panel shows
              a clear "self-update unavailable" message with the specific reason instead of failing
              silently, alongside the manual fallback (<Code>git pull &amp;&amp; docker compose up -d
              --build</Code>). Setting <Code>PROJECT_DIR</Code> in <Code>.env</Code> to this repo's
              absolute path on the host overrides the automatic lookup entirely, for the rare setup where
              it can't work. There's no automatic rollback: if a migration or
              restart fails partway through, whatever containers are already running keep running on what
              they had, check the panel's error message and container logs to recover.
            </P>
          </>
        ),
      },
      {
        id: "audit-log",
        title: "Audit log",
        overview: (
          <>
            <P>
              Every organization has its own append-only audit trail of who changed what and when, useful
              for accountability across a team and for tracing back an unexpected change.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              Each entry records the action, the target type/ID, the acting user, a timestamp, and a JSON
              detail blob. Coverage includes login/logout/2FA changes, force-logout, create/update/delete
              on users, organizations, hypervisors, disk layouts, templates, ISO assets, and webhooks,
              template/disk-layout export/import, settings changes (including branding, M365 config,
              HTTPS certificate uploads/mode switches, and self-update triggers), and deployment
              create/retry/power/delete actions. It's paginated and exportable to CSV from the Audit Log
              page.
            </P>
            <P>
              For a detailed blow-by-blow of one specific deployment attempt (not who triggered it, but
              exactly what the pipeline did), use that deployment's own log stream and{" "}
              <strong>Download full log</strong> button instead, the audit log intentionally doesn't
              duplicate that level of detail (individual ISO chunk uploads, for example, aren't logged
              here, only the create/finalize that bookend them).
            </P>
          </>
        ),
      },
      {
        id: "settings-timeout",
        title: "Settings and the deployment timeout",
        overview: (
          <>
            <P>
              Settings is split into instance-wide options (global admins only: branding, updates, HTTPS
              certificate, email, backups) and per-organization options (the deployment timeout, plus
              anything else you set).
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              The one per-organization setting with real day-to-day relevance is the{" "}
              <strong>deployment timeout</strong>: a deployment stuck past this many minutes in any
              non-terminal stage is force-failed automatically and cleaned up. It defaults to 90 minutes
              and has its own labeled field on the Settings page; an "Advanced" section underneath still
              exposes the raw key/value store directly, for anything that doesn't have a dedicated field
              yet.
            </P>
            <P>
              Panels that have more than one field to change use a single <strong>Apply changes</strong>{" "}
              button for the whole panel, not one save button per field, so changing several things at
              once is one action, not several.
            </P>
          </>
        ),
      },
    ],
  },
  {
    id: "customization",
    label: "Customization",
    articles: [
      {
        id: "branding",
        title: "Branding your instance",
        overview: (
          <>
            <P>
              DeployCore ships with its own default look (an icon and name in the sidebar and sign-in
              screen), but a global admin can replace it with your own MSP's name and logo in a couple of
              clicks, so the people using it see your brand, not a generic dashboard.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              Settings → MSP Organization: set the instance name (shown in the sidebar and sign-in
              screen), and optionally upload a logo (PNG, JPEG, or SVG, up to 5&nbsp;MB, transparent
              backgrounds are preserved, so a mark cut to just the logo itself looks best). Once uploaded,
              your logo replaces DeployCore's own default icon everywhere it appears; removing it reverts
              to the default rather than showing nothing.
            </P>
            <P>
              Every user can additionally set their own profile picture from the Account page (PNG or
              JPEG, under 2&nbsp;MB), shown next to their name in the sidebar and in the Users list. This
              is independent of the instance-wide logo, both can be set at once.
            </P>
            <P>
              Dark mode (toggle in the header) is remembered per browser and applies consistently across
              every page, including the sign-in and setup-wizard screens, which also sit on a subtly
              animated background matching whichever theme is active.
            </P>
          </>
        ),
      },
    ],
  },
];
