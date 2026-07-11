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
        id: "installation-and-setup",
        title: "Installation and setup",
        overview: (
          <>
            <P>
              DeployCore installs with one script and configures itself through a one-time setup wizard
              on first launch, no manual database or config-file editing needed either time.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              <Code>scripts/setup.sh</Code> generates <Code>APP_SECRET_KEY</Code> if you leave it blank,
              the Fernet key DeployCore encrypts every stored credential and password with (hypervisor
              credentials, local admin/domain-join passwords, M365 client secret) and signs JWTs with,
              then builds and starts every container with Docker Compose. Database migrations run
              automatically on every <Code>api</Code> container start, both for this first install and
              every later update, there's no separate migration step to remember.
            </P>
            <P>
              It also detects this host's own LAN-facing IP and writes it into <Code>APP_PUBLIC_URL</Code>{" "}
              (skipped if that's already set to something other than the shipped default), the address
              guest VMs call back to once Windows Setup finishes, so a fresh install works without having
              to know to set that by hand, see "Troubleshooting a failed or stuck deployment" and "HTTPS
              certificate" for exactly why that setting matters and needs to be a real, VM-reachable
              address rather than <Code>localhost</Code>. It's a best-effort guess, not a guarantee: on a
              host with multiple network interfaces, or if your VMs actually land on a different network
              than whichever one <Code>setup.sh</Code> happened to detect, open <Code>.env</Code>, correct{" "}
              <Code>APP_PUBLIC_URL</Code> yourself, then <Code>docker compose up -d</Code> to apply it.
            </P>
            <P>
              The setup wizard itself appears the first time you open the app: it asks for an instance
              name (shown in the sidebar and browser tab until you set a logo, see "Branding your
              instance") and creates the first admin account (username and password required, email
              optional, see "Users, roles, and permissions" for what email is used for). It also creates a
              default global disk layout automatically, so a brand-new instance already has one to pick
              from the first time you build a template. The wizard is a one-time, one-way door: once any
              user account exists, its endpoint locks out with a <Code>409</Code> and it never shows again,
              new users after that are created from the Users page instead.
            </P>
            <P>
              Once running, keeping DeployCore current is a single click, not a redo of any of this, see
              "Self-update" for exactly how that works and what it does and doesn't touch.
            </P>
          </>
        ),
      },
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
            <P>
              The Dashboard is per-organization by default (running/completed/failed deployment counts,
              hypervisor connection health, and the 8 most recent deployments for whichever organization
              is currently selected). A global admin additionally gets a cross-organization overview: one
              row per organization with those same counts side by side, click a row to switch the active
              organization instead of using the picker in the header.
            </P>
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
              place). The confirmation dialog itself is intentionally short, this article is the full
              detail behind it.
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
                "API endpoint: the ESXi/vCenter host's IP address or hostname",
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
              finalize call assembles the file, checksums it, and (for a Windows ISO) patches out the
              install media's own "Press any key to boot from CD or DVD..." prompt, see "Unattended
              Windows Setup, in depth" for exactly how. Chunking is deliberate: multi-gigabyte ISOs upload
              reliably without ever holding the whole file in memory on either end, and a flaky connection
              just means a slower upload, not a failed one.
            </P>
            <P>
              For a Windows ISO, finalize also detects every Windows edition bundled inside its{" "}
              <Code>install.wim</Code> (Windows Server media typically ships several: Standard/Datacenter
              crossed with Server Core/Desktop Experience, all in one file, selected only by an install-time
              index). This reads the WIM's own embedded XML metadata, it doesn't need to actually install
              anything to know what's available, including which editions actually have a GUI (Desktop
              Experience) versus which are Server Core, read from the WIM's own edition flags rather than
              guessed from the edition's name. Templates use this list to offer a named, GUI/Core-labeled
              edition picker instead of a bare number, see "Templates and Windows roles". An ISO with no
              detectable editions (non-Microsoft media, or an ISO uploaded before this existed) just falls
              back to a plain image-index number field, silently, this never blocks or fails the upload
              itself. If a real Windows ISO unexpectedly gets no editions detected, the reason (couldn't
              find <Code>install.wim</Code>/<Code>install.esd</Code>, extraction failed, malformed
              metadata, ...) is logged on the <Code>api</Code> container, not shown anywhere in the UI:
              <Code>docker compose logs api</Code> right after the upload.
            </P>
            <P>
              This step uses <Code>7z</Code>, not the <Code>xorriso</Code> tool the boot-prompt patch
              above relies on: real Windows Setup media lays itself out as a UDF-plus-ISO9660 hybrid
              specifically so <Code>install.wim</Code> can be larger than the 4&nbsp;GiB plain-ISO9660
              limit (a multi-edition Server WIM routinely is), and <Code>xorriso</Code> can silently
              truncate a file that large when extracting it from that layout, no error, just a corrupted
              copy, whereas <Code>7z</Code> reads the underlying UDF filesystem directly and doesn't have
              that problem.
            </P>
            <P>
              Deleting an ISO asset removes the file from disk and the database record. If any template
              still references it, that template's ISO is cleared rather than the delete being blocked,
              the template survives but can't deploy until a new ISO is attached, same as a brand new
              template that's never had one set.
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
        id: "app-assets",
        title: "App assets",
        overview: (
          <>
            <P>
              An installable piece of software, an RMM/monitoring agent, antivirus, a line-of-business app,
              anything with a silent-install flag, uploaded once and attached to as many templates as you
              like. A template installs its attached apps automatically after Windows Setup finishes, no
              manual RDP-in-and-double-click-the-installer step needed.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              Same upload mechanics as ISO assets: chunked from the browser, org-scoped or global (a
              global admin gets the same "Available to" choice, handy for an agent every customer
              environment should get). Three fields beyond the file itself: a <strong>display name</strong>{" "}
              (independent of the uploaded filename, so "Datto RMM Agent" can be what operators see even
              if the file itself is <Code>AgentSetup_2024.1.exe</Code>), whether it's an <strong>MSI or
              EXE</strong>, and <strong>default silent-install arguments</strong> (e.g. <Code>/qn
              /norestart</Code> for an MSI, or whatever an EXE's own convention is, commonly{" "}
              <Code>/S</Code>, <Code>/silent</Code>, <Code>/verysilent</Code>, or <Code>/quiet</Code>,
              check the vendor's docs, there's no universal standard for EXEs the way MSI has one).
            </P>
            <P>
              A template attaches any number of app assets in an ordered list (Templates page, the
              "Software to install" section), each with its own optional argument override, blank means
              "use the asset's own default." Installed over WinRM during post_install, after Windows
              features and before post-install scripts, so a script can assume an app installed earlier in
              the list is already there by the time it runs.
            </P>
            <P>
              Delivery is guest-initiated: the guest's own <Code>Invoke-WebRequest</Code> downloads each
              installer directly from DeployCore, the worker never pushes file bytes through WinRM itself
              (the same reasoning as the Setup-complete callback: the guest already reaches DeployCore's
              API, so there's no need to chunk a multi-hundred-MB installer through WinRM's own message-size
              limits). That download endpoint is authenticated by a random, single-deployment token
              generated right before the first app install and cleared right after (or on failure), there's
              no user session to authenticate the guest with, the same pattern as the callback token itself.
              MSI installs run through <Code>msiexec /i "&lt;path&gt;" &lt;args&gt;</Code>; EXE installs run
              the downloaded file directly with the arguments passed straight through. Either way, exit
              code <Code>3010</Code> (success, reboot required) counts as success alongside <Code>0</Code>.
            </P>
            <P>
              Deleting an app asset removes the file and the database row immediately, it isn't a foreign
              key the way <Code>iso_asset_id</Code> is: a template's attached apps are just a list of ids
              in a JSON column. A template that still lists a deleted app skips it at deploy time (logged
              as an error) rather than failing the whole deployment or blocking the delete.
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
                <>The Windows <strong>edition</strong> to install from that ISO's <Code>install.wim</Code>. If
                  the ISO asset has detected editions (see "ISO assets"), this is a dropdown of the actual
                  edition names as Microsoft's own media names them (e.g. "Windows Server 2025 Standard
                  (Desktop Experience)"), so you're picking a real edition instead of a bare number,
                  otherwise it's a plain image-index number. This defaults to index 1, which is{" "}
                  <em>not</em> a considered default, it's whatever Microsoft's media happens to put first,
                  typically Server Core (no GUI) on standard multi-edition Server ISOs, check the dropdown
                  rather than assuming. Detection also works out whether each edition actually has a GUI
                  from the WIM's own <Code>FLAGS</Code> metadata (a Core edition's flag always ends in{" "}
                  <Code>Core</Code>, e.g. <Code>ServerStandardCore</Code>, more reliable than the edition
                  name/description text, which isn't guaranteed to spell "(Desktop Experience)" out on
                  every ISO), stored on the edition for future use even though the dropdown itself doesn't
                  show it as a separate tag today, trusting Microsoft's own naming to already be clear.</>,
                <>Disk layout, CPU count and cores per socket, RAM (MB), disk size (GB) and its{" "}
                  <strong>provisioning type</strong>: thin (space allocated on demand), thick lazily zeroed
                  (space reserved up front, zeroed on first write), or thick eagerly zeroed (space reserved
                  and zeroed entirely at creation time, slower to create but avoids any first-write
                  latency, the option most production databases and similar disk-latency-sensitive
                  workloads want).</>,
                <>Network name, this is the ESXi/vCenter <strong>port group or vSwitch name</strong> exactly
                  as it appears in your hypervisor's networking configuration, not a Windows-side network
                  name, it's what the new VM's virtual NIC attaches to. There's no separate VLAN ID field:
                  network segmentation is expected to already be handled by picking the right port group
                  (one per VLAN, set up on the hypervisor side), not by tagging the VM itself. Also its{" "}
                  <strong>adapter type</strong> (VMXNET3, the paravirtualized default with the best
                  performance and the one to use unless you have a specific reason not to; E1000/E1000E
                  emulate real Intel NICs, only needed for guest OS or driver compatibility). Also
                  locale/timezone/keyboard layout as Windows identifiers, not
                  IETF/IANA ones (new templates default to <Code>de-DE</Code>/<Code>W. Europe Standard
                  Time</Code>/<Code>de-CH</Code>; see "Unattended Windows Setup, in depth" for how keyboard
                  layout resolves to an exact physical layout rather than just a language).</>,
                <>Local administrator password, always write-only, never shown again after saving. Off by
                  default, an optional <strong>custom admin account</strong> toggle: off just sets that
                  password on the built-in Administrator account, no other change. On adds a username field
                  (default <Code>svcadmin</Code>, can't be a reserved Windows account name, i.e.{" "}
                  <Code>Administrator</Code>, <Code>Guest</Code>, <Code>DefaultAccount</Code>,{" "}
                  <Code>WDAGUtilityAccount</Code>) and creates that as a genuinely new local account instead,
                  the built-in Administrator account gets disabled within seconds of first boot in that
                  case, see "Unattended Windows Setup, in depth."</>,
                <>Optional domain join: FQDN, join account, join credential (write-only), target OU, and
                  timing, either <Code>answer_file</Code> (baked into the unattended install) or{" "}
                  <Code>post_install</Code> (joined afterward over WinRM).</>,
                "A curated list of Windows roles/features, picked as checkboxes: AD Domain Services, DNS, DHCP, Web Server (IIS), Print Services, Remote Desktop Session Host, DFS Namespaces, DFS Replication.",
                <>An ordered list of <strong>app installs</strong>, App Assets to install automatically
                  (see that article), with an optional argument override per attachment. Installed after
                  roles, before post-install scripts.</>,
                "Any number of post-install PowerShell scripts (name + script text), run in order after roles and app installs.",
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
            <P>
              <strong>Delete</strong> always succeeds even if deployments were created from that template:
              a deployment doesn't need its template to exist afterward, it keeps its own rendered answer
              file and settings, deleting only clears the now-dangling reference on those deployment rows
              rather than being blocked by them. The one edge case is a deployment that's still actively
              provisioning (or gets retried) after its template was deleted mid-flight, that fails with an
              explicit error in its log rather than deploying from a template that no longer exists.
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
                  ISO and the floppy to the hypervisor datastore, creates the VM (UEFI firmware, LSI Logic
                  SAS controller, not VMware's own PVSCSI, see "Unattended Windows Setup, in depth" for
                  why), attaches media, and powers on. The Windows ISO itself is only ever uploaded
                  to a given hypervisor's datastore once per ISO asset, not once per deployment: a second
                  deployment from the same template (or a bulk deployment creating many at once) reuses
                  the copy already there instead of re-transferring a multi-gigabyte file every time.</>,
                <>The whole point is zero interaction: no "press any key" prompt (patched out of the ISO
                  itself at upload time), no interactive language/keyboard screen, no clicking through
                  OOBE. See <strong>"Unattended Windows Setup, in depth"</strong> for exactly how the boot
                  prompt, the answer-file delivery, and locale/keyboard get handled, and what the two
                  remaining synthetic-keypress fallbacks are actually for.</>,
                <>The deployment moves into <Code>installing_os</Code> right after the VM is powered on
                  and DeployCore has done everything it can from its side (boot-order, synthetic
                  keypresses), not when Windows Setup actually finishes: there's no way to observe real
                  progress inside WinPE/Setup at all, so this state simply covers that entire black-box
                  window rather than staying <Code>booting</Code> for it and jumping straight to done. The
                  guest calls back to DeployCore once Windows Setup actually finishes (a single-use token
                  per deployment, <Code>callback_token_used</Code>, checked rather than the state itself,
                  since the state was already <Code>installing_os</Code> before the callback exists to
                  react to). That callback is the point DeployCore is sure Setup is done with the install
                  media for good: it ejects the Windows/VirtIO ISOs, removes the floppy device, and deletes
                  the per-deployment answer-file floppy from the datastore, all best-effort and never worth
                  failing an otherwise-successful deployment over. Nothing from here on (post-install runs
                  entirely over WinRM) needs any of it.</>,
                <>A static deployment's IP/netmask/gateway/DNS are set declaratively in the answer file
                  itself (the specialize pass, before Windows Setup even finishes), not reconfigured
                  over WinRM afterward, that would mean connecting at whatever address DHCP handed out
                  first and reassigning it remotely, which can't work at all on a network with no DHCP
                  server to hand out that first address. Post-install then runs over WinRM once the
                  guest reports an IP, its static one directly for a static deployment: install each
                  selected Windows role, install each attached app asset in order (see "App assets"),
                  run post-install scripts in order, join the domain here if configured for that timing,
                  reboot, verify it comes back reachable, then mark the deployment completed.</>,
                <>A stuck deployment (past its configured timeout, default 90 minutes, editable per
                  organization in Settings) is force-failed automatically by a background job, and cleaned
                  up the same way a real failure would be.</>,
                <>A completed deployment gets a health check every 15 minutes, a TCP connect to port 3389
                  (RDP) on its guest IP, not WinRM, which is deliberately closed for good by this point, see
                  "Unattended Windows Setup, in depth." Up to 30 days of history is shown as a strip of
                  badges on its detail page; going from healthy to unreachable also fires a
                  notification/webhook.</>,
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
              stops tracking it, it's no longer reachable through this deployment or this UI at all (remove
              it directly on the hypervisor if you want it gone too), and if the pipeline is still actively
              running in the background worker, deleting the deployment doesn't cancel it, that keeps going
              too, just with nothing in the UI showing it anymore.
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
        id: "unattended-setup",
        title: "Unattended Windows Setup, in depth",
        overview: (
          <>
            <P>
              Getting a Windows Server install to run with genuinely zero interaction, no "press any key,"
              no language/keyboard screen to click through, no OOBE, took several rounds of real-world
              testing against a physical host to get right. This article is the full account of how each
              piece works, since the short version in the Deployments article doesn't have room for it.
            </P>
          </>
        ),
        deepDive: (
          <>
            <P>
              <strong>Disk controller: LSI Logic SAS, not PVSCSI.</strong> This is the first thing that
              can go wrong, before Setup even boots. VMware's own paravirtualized SCSI controller
              (PVSCSI) has real performance advantages, but Windows has no in-box driver for it at all:
              a disk attached to a PVSCSI controller can't be recognized during Setup, or on the guest's
              own later boots, without the VMware Tools PVSCSI driver injected into the install media
              first (<Code>PnpCustomizationsWinPE</Code>/<Code>DriverPaths</Code>, plus registering it as
              a boot-critical driver), which this pipeline doesn't do, there's no injection step anywhere
              in it. Depending on the exact Windows build and ESXi version, that can look like it's
              working, WinPE can sometimes still read/write the disk well enough to partition it and copy
              files, right up until the point Setup reboots to continue on the disk it just installed to
              and the guest's own kernel can't find a driver for the controller it's booting from, which
              surfaces as a generic "the computer was unexpectedly restarted" dialog with no useful detail
              about the real cause. LSI Logic SAS has been a Windows in-box driver for every Server
              version this tool targets for well over a decade, no injection needed, at the cost of
              PVSCSI's IOPS/CPU-overhead edge, worth it for a pipeline that has to boot correctly with
              nobody watching every single time.
            </P>
            <P>
              <strong>Console mouse cursor.</strong> Every VM also gets a USB 3.0 (xHCI) controller added
              right after creation (best-effort, a separate step from VM creation itself so a failure here
              never blocks a deployment): ESXi presents an absolute-positioning USB tablet over it
              automatically, no VMware Tools required. Without it, a fresh guest only has the default PS/2
              mouse, which the ESXi/vSphere web console can't track properly, the cursor doesn't reliably
              show up or move with the actual pointer at all, only really noticeable if you open the
              console yourself (post-install itself is entirely WinRM-driven, it never needs the console or
              a working mouse for anything). Confirmed against Packer's own <Code>vsphere-iso</Code> builder
              source, which constructs exactly <Code>VirtualUSBXHCIController</Code> with no other
              properties for its own <Code>"xhci"</Code> option, documented there as specifically "needed
              for mouse during install without VMware Tools."
            </P>
            <P>
              <strong>The "Press any key to boot from CD or DVD..." prompt.</strong> This isn't part of
              Windows Setup, it's the Windows install ISO's own UEFI boot loader, and Setup has no say over
              it via <Code>autounattend.xml</Code>, there's no answer-file setting that suppresses it.
              Every stock Windows install ISO ships two UEFI boot images in its El Torito boot catalog: the
              normal one that prints the prompt and waits indefinitely for a keystroke, and a second,
              silent one (<Code>efi/microsoft/boot/efisys_noprompt.bin</Code>) that Microsoft built for
              exactly this scenario, unattended deployment tooling like WDS/MDT relies on it. DeployCore
              patches this once, the moment a Windows ISO finishes uploading (not per deployment): it finds
              both boot images inside the ISO and rewrites the boot catalog's actual UEFI entry to point at
              the silent one's content instead, using <Code>xorriso</Code> in "modify, don't fully rebuild"
              mode so nothing else about the ISO changes. If a particular ISO doesn't have that second boot
              image (non-standard media), the swap is skipped and the ISO is left exactly as uploaded
              rather than failing the upload over it.
            </P>
            <P>
              <strong>Delivering the answer file.</strong> It ships on a floppy image, not a second
              CD-ROM. A second CD-ROM does work for most of the answer file (Windows Setup finds and
              applies it: disk partitioning, install, domain join, admin password, all unattended), but
              empirically not reliably for the very first implicit check, the one deciding whether to show
              the interactive language/time/keyboard screen at all, which runs before Setup's driver stack
              is fully up. A floppy is both checked earlier and higher-precedence there, per Microsoft's
              own documented implicit-search order for removable media, which is what makes it reliable
              for that one specific check.
            </P>
            <P>
              <strong>Locale, system locale, and keyboard layout.</strong> These get set in two separate,
              differently-scoped places in the answer file, and DeployCore didn't originally set both:
            </P>
            <List
              items={[
                <><Code>Microsoft-Windows-International-Core-WinPE</Code> in the windowsPE pass covers only
                  Windows Setup's own UI while it's running, it has zero effect on the OS once installed.</>,
                <><Code>Microsoft-Windows-International-Core</Code> in the specialize pass covers the
                  actually-installed OS, the built-in Administrator account and every new user profile. This
                  is the one that was missing: even a perfectly applied answer file could leave the deployed
                  machine itself on a default keyboard, since nothing had ever told the installed OS what to
                  use. DeployCore sets both now.</>,
              ]}
            />
            <P>
              Keyboard layout is rendered as an explicit <Code>LCID:KLID</Code> hex pair (e.g.{" "}
              <Code>0807:00000807</Code> for German (Switzerland)), not the bare locale tag you type into
              the Templates form, because a bare tag only picks <em>a</em> default keyboard for that locale,
              not necessarily the one named after it, a bare <Code>de-CH</Code> lands on the plain German
              layout, not Swiss German. DeployCore resolves this automatically for every locale it has a
              known keyboard mapping for (the German-, French-, and Italian-speaking European locales and
              their neighbors, see the Templates page's field hint); type a value that already contains a{" "}
              <Code>:</Code> and it's passed through untouched instead, for anything outside that table or
              a non-default keyboard on a locale that is in it.
            </P>
            <P>
              Windows Setup's own windowsPE-stage screen has a long-standing, still-open upstream quirk
              (reported by others against multiple Windows versions, on locales other than the ones
              DeployCore was tested against) where a small set of locales don't get their{" "}
              <Code>InputLocale</Code> honored on that one specific screen, even though every other part of
              the answer file, including the specialize-pass value above, applies correctly. Nobody in the
              community has a clean, answer-file-only fix for it. This is what the second, sparser round of
              synthetic Enter keypresses (below) exists to get past, if it shows up; it has nothing to do
              with locale correctness, which is already handled by the time that screen would appear.
            </P>
            <P>
              <strong>Boot order: disk before CD-ROM, not the other way around.</strong> This is a
              documented Packer/vSphere-iso gotcha, not anything specific to this setup, but it's easy to
              get backwards. On the very first boot the disk is blank, the firmware fails it and falls
              through to the CD-ROM within the same boot pass, which is exactly what ESXi's{" "}
              <Code>bootRetryEnabled</Code>/<Code>bootDelay</Code> are tuned for (they also cover a freshly
              attached CD-ROM not being fully connected the instant the VM powers on). Windows Setup writes
              a bootloader to the disk partway through installation and reboots to continue there; with
              CD-ROM first in the boot order, that reboot lands back in the CD's own WinPE instead, and
              Setup throws "the computer was unexpectedly restarted" since it finds itself back in a fresh
              WinPE with no memory of the in-progress install, well before OOBE or any WinRM/callback
              activity, which is also why this specific failure never shows up in the deployment's log
              stream, DeployCore has no visibility into the guest yet at that point. Disk-first avoids it
              entirely: once the disk has a bootloader, every later boot goes straight to it and the
              CD-ROM is never touched again.
            </P>
            <P>
              <strong>The two keypress fallbacks that remain.</strong> With the boot-prompt ISO patch in
              place, most deployments never need either of these, they're safety nets, not the primary
              mechanism:
            </P>
            <List
              items={[
                <>A tight round right after power-on, one synthetic Enter per second for about 15 seconds,
                  for an ISO the boot-prompt patch was skipped on (or any other firmware prompt). Sized to
                  bracket when a "press any key" prompt would appear and stop before Setup's GUI is up,
                  where blind Enters would start landing on real dialogs instead.</>,
                <>A sparser round after that, one Enter every 8 seconds for about two minutes, timed for
                  whenever Setup's GUI actually finishes loading (which varies far more than the boot
                  prompt's own timing), for the windowsPE-stage language/keyboard screen quirk above.</>,
              ]}
            />
            <P>
              <strong>OOBE and the built-in Administrator account.</strong> The <Code>OOBE</Code> block
              (<Code>HideEULAPage</Code>, <Code>HideLocalAccountScreen</Code>,{" "}
              <Code>HideOEMRegistrationScreen</Code>, <Code>HideOnlineAccountScreens</Code>,{" "}
              <Code>HideWirelessSetupInOOBE</Code>, <Code>NetworkLocation</Code>,{" "}
              <Code>ProtectYourPC</Code>, <Code>SkipMachineOOBE</Code>, <Code>SkipUserOOBE</Code>)
              suppresses every interactive OOBE screen: account setup, network location, and the
              diagnostic-data/privacy consent page. The last two settings, <Code>NetworkLocation</Code>{" "}
              and <Code>ProtectYourPC</Code>, matter specifically because the Hide*/Skip* flags alone
              don't cover them — <Code>ProtectYourPC</Code> has no default value at all, and Microsoft's
              own documentation says plainly that leaving it unset opens the "Get going fast" diagnostics
              page during setup regardless of every other flag, confirmed live in testing (that page, and
              the network-location prompt, both still showed up on manual first logon with only the
              Hide*/Skip* flags set). But <strong>on its own the OOBE block still doesn't get anyone
              logged in</strong>: <Code>FirstLogonCommands</Code> only ever run as part of an actual
              first-logon event, and without something making that happen unattended, Setup leaves a
              plain login prompt sitting at the console, exactly the manual step this whole tool exists
              to remove. The declarative <Code>AutoLogon</Code> element was tried for that, targeting
              whichever account <Code>local_admin_username</Code>/<Code>local_admin_password</Code>{" "}
              resolve to — but it's <strong>not in the answer file</strong>: every deployment on real
              hardware that included it failed outright during Setup regardless of where in oobeSystem
              it was placed, isolated by ruling out static-vs-DHCP networking, hostname length, and the
              OOBE settings above via controlled, one-variable-at-a-time tests, and confirmed when a real
              deployment finally completed once <Code>AutoLogon</Code> was removed entirely. Auto-logon
              is still configured, just differently: four <Code>RunSynchronousCommand</Code> entries in
              the specialize pass's <Code>Microsoft-Windows-Deployment</Code> component, each running one{" "}
              <Code>reg.exe add</Code>, write the same registry values (<Code>AutoAdminLogon</Code>,{" "}
              <Code>DefaultUserName</Code>, <Code>DefaultPassword</Code>, <Code>AutoLogonCount</Code>) a
              working <Code>AutoLogon</Code> element would itself write — Winlogon reads those at
              first-logon time the same way no matter which mechanism put them there, so the effect is
              identical, just reached without whatever Setup-time processing of the declarative element
              was breaking on this environment. The first attempt at this used a single{" "}
              <Code>powershell.exe -Command</Code> one-liner instead of <Code>reg.exe</Code>, and that
              broke Setup a different way: a hard crash immediately after landing in the specialize pass
              ("the computer was unexpectedly restarted, or an unexpected error occurred"), consistent
              with a real, documented failure mode where PowerShell cmdlets in{" "}
              <Code>RunSynchronousCommand</Code> crash this early because WMI/CIM and other subsystems it
              can depend on aren't fully initialized yet. <Code>reg.exe</Code> has no such startup
              dependency, which is why it's what actually runs now; command lines are built with Python's{" "}
              <Code>subprocess.list2cmdline</Code> for correct Win32 argv quoting (<Code>Path</Code>{" "}
              launches the executable directly, no <Code>cmd.exe</Code> involved, so none of{" "}
              <Code>cmd</Code>'s own <Code>%</Code>/<Code>^</Code>/<Code>&amp;</Code> metacharacter
              handling applies — just the same quoting rules <Code>CreateProcess</Code> itself expects).
              One side effect worth knowing, same as the element had:
              this leaves that password in plaintext in the registry
              (<Code>HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon</Code>) as an
              implementation detail of how it works at all; the very first <Code>FirstLogonCommand</Code>,
              before anything else including enabling WinRM, scrubs both values out again, keeping the
              window that plaintext copy exists as short as possible. Setup always requires the built-in
              Administrator account to have a password at this point too
              (<Code>AdministratorPassword</Code>), so it always gets one regardless of which account
              ends up being the one that's actually used.
            </P>
            <P>
              A template's <strong>custom admin account</strong> toggle (Templates page, off by default)
              controls what happens beyond that. Off: nothing else, the built-in Administrator account with
              that password is the account the deployed machine actually has. On: a separate, declarative{" "}
              <Code>LocalAccounts</Code> entry in the same <Code>UserAccounts</Code> block additionally
              creates a genuinely new local account (the template's own username/password, a member of{" "}
              <Code>Administrators</Code>), and two more <Code>FirstLogonCommands</Code> render to make it
              the account actually meant to survive: first, <Code>LocalAccountTokenFilterPolicy</Code> gets
              set to 1 in the registry, without it Windows' UAC remote restriction only exempts the true
              built-in Administrator (RID 500) from a filtered, non-elevated token on network logons, which
              would silently break every WinRM command DeployCore runs post-install for the custom account
              despite it being in Administrators. Second, after the callback to DeployCore has already
              fired (deliberately last, so a guest-side quirk from disabling the very account{" "}
              <Code>FirstLogonCommands</Code> is running as can never risk that callback),{" "}
              <Code>Disable-LocalUser -Name 'Administrator'</Code> deactivates the built-in account for
              good. The API enforces the pairing server-side regardless of what a client sends:{" "}
              <Code>local_admin_username</Code> is always forced back to <Code>"Administrator"</Code> when
              the toggle is off, and rejected if it's a reserved Windows account name when the toggle is on.
            </P>
            <P>
              <strong>Cleaning up after Setup is done.</strong> The guest's <Code>FirstLogonCommands</Code>{" "}
              call back to DeployCore only once Windows Setup has fully finished, OOBE included, and the
              guest has booted into the installed OS for the first time, that's the one point it's safe to
              say the install media is no longer needed by anything (post-install runs entirely over WinRM
              afterward, authenticated as <Code>local_admin_username</Code>, whichever account that
              actually is). That callback is what DeployCore waits for before it ejects the Windows and
              VirtIO ISOs (drive kept, just emptied, matching how you'd eject a real disc), removes the
              floppy device entirely (it only ever had one job), and deletes the per-deployment answer-file
              floppy image from the datastore. All of this is best-effort: a failure here is logged but
              never fails an otherwise-successful deployment.
            </P>
            <P>
              <strong>Closing WinRM once post-install is done.</strong> WinRM is only ever needed while
              DeployCore itself is actively configuring the guest; leaving it open indefinitely afterward
              is unnecessary attack surface. The very last WinRM action of a deployment, right before it's
              marked <Code>completed</Code>, removes the WinRM firewall rule, runs{" "}
              <Code>Disable-PSRemoting -Force</Code>, and stops and disables the WinRM service itself. That
              last part runs in a short-delayed detached process rather than inline, so the command doesn't
              try to report its own success back over the exact channel it's in the middle of tearing down.
              From this point on a completed deployment has no WinRM listener at all, by design: there's no
              way for DeployCore (or anything else) to remotely reconfigure the guest again without an
              operator opening it back up on the guest directly.
            </P>
            <P>
              This is also why the post-deploy health check (see the Deployments article) doesn't use
              WinRM: it can't, by the time a deployment reaches <Code>completed</Code> nothing is listening
              on 5985 anymore. It checks a plain TCP connect to port 3389 (RDP, on by default on every
              Windows Server SKU) instead, proof the guest OS is up and reachable on the network, not that
              remote management still works, since deliberately nothing does.
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
                <><strong><Code>installing_os</Code> for the whole time Windows Setup is running is
                  expected</strong>, not stuck: that state covers the entire black-box window from "VM
                  powered on" to "guest called back," there's no way to observe real progress inside
                  WinPE/Setup, so it's normal for this to be by far the longest-lived state, typically the
                  bulk of a deployment's total time. <Code>booting</Code> itself should be brief (VM
                  power-on plus the synthetic boot keypresses), it moves into{" "}
                  <Code>installing_os</Code> on its own well before Setup is actually done installing
                  anything.</>,
                <><strong>Actually stuck (past the timeout, force-failed with a callback-timeout
                  error)?</strong> The guest calls back to DeployCore once Windows Setup finishes; if that
                  callback can never arrive (<Code>APP_PUBLIC_URL</Code> isn't reachable from the VM's
                  network) the deployment sits in <Code>installing_os</Code> until its timeout trips.
                  Confirm <Code>APP_PUBLIC_URL</Code> really is reachable from the organization's
                  hypervisor network, not just from your own machine, that it's this host's actual LAN
                  address rather than <Code>localhost</Code> (which resolves to the guest VM itself when
                  that string ends up inside a command running on it, not to DeployCore), and that it's
                  still plain <Code>http://</Code> on port 8000, not routed through the HTTPS proxy, see
                  "HTTPS certificate" for why.</>,
                <><strong>Fails immediately with "template has no Windows ISO configured"?</strong> The
                  template was created (or exported/imported) before an ISO was attached to it, attach one
                  on the Templates page.</>,
                <><strong>The console shows "the computer was unexpectedly restarted, Windows
                  installation cannot continue" after the VM reboots to continue installing?</strong> This
                  is Windows Setup's own generic dialog for a specialize-pass answer-file failure, and
                  DeployCore has no visibility into it at all, since it happens before the guest has ever
                  reached a state where it could call back (nothing shows in the log stream, the
                  deployment just eventually times out). The most common cause by far: the hostname is
                  longer than 15 characters. <Code>ComputerName</Code> in the specialize pass is a NetBIOS
                  name, hard capped at 15, and Windows doesn't truncate a longer value the way it does for
                  some other name fields, it fails to process the whole answer file instead. DeployCore
                  now blocks this at submission (both the wizard and the API reject a hostname/bulk prefix
                  that doesn't fit), but a deployment created before that validation existed, or an
                  answer file edited by hand, can still hit it.</>,
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
            <P>
              This certificate only covers <strong>browser</strong> traffic to the UI (and, through
              the frontend's own dev proxy, the API calls your browser makes). It has nothing to do
              with how a guest VM calls back to DeployCore during provisioning, that path
              (<Code>APP_PUBLIC_URL</Code>) intentionally stays plain <Code>http://</Code> straight to
              the <Code>api</Code> container's own directly-exposed port 8000, bypassing this proxy
              entirely, because a fresh Windows guest's default PowerShell has no easy way to trust (or
              skip validating) a self-signed certificate. See "Troubleshooting a failed or stuck
              deployment" if a deployment is stuck waiting for that callback.
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
              exposes the raw key/value store directly (value can be plain text or JSON, stored as JSONB
              either way), for anything that doesn't have a dedicated field yet.
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
