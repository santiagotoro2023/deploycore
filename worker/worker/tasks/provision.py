import asyncio
import secrets
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import or_, select

from app.config import get_settings
from app.db import SessionLocal
from app.hypervisors import get_driver
from app.hypervisors.base import HypervisorDriver, PowerState, VmSpec
from app.hypervisors.defaults import HYPERVISOR_DEFAULTS, generate_mac_address
from app.models.app_asset import AppAsset
from app.models.deployment import Deployment, DeploymentState, IpMode, LogLevel
from app.models.disk_layout import DiskLayout
from app.models.hypervisor import HypervisorHost
from app.models.iso_asset import IsoAsset, IsoKind
from app.models.managed_host import ManagedHost
from app.models.template import DeploymentTemplate, DomainJoinTiming
from app.services import notifications, remote_session, settings_resolver, webhooks
from app.services.deployment_service import DeploymentStateMachine, InvalidTransition, log
from app.services.floppy_builder import build_and_upload_answer_floppy
from app.services.template_effective import resolve_template
from app.services.template_render import render_autounattend
from app.winrm.client import WinRMClient

_state_machine = DeploymentStateMachine()

# _fail preserves the VM instead of deleting it when the deployment had
# already reached one of these states: Windows Setup itself already
# succeeded and the guest was already WinRM-reachable by that point, so
# there's a perfectly good, bootable install sitting there - only a
# post-install step (a script, a feature, an app) failed. Deleting it
# and starting a whole new VM from scratch for what's often a one-line
# script fix is exactly the "wait forever for a new VM" pain retry-
# post-install exists to avoid - see deployment_service.retry_post_install.
_KEEPABLE_ON_FAILURE_STATES = {DeploymentState.POST_INSTALL, DeploymentState.CONFIGURING}

WINDOWS_ISO_UNIT = 0
VIRTIO_ISO_UNIT = 1

CALLBACK_POLL_INTERVAL_SECONDS = 15

# The callback is FirstLogonCommands' own outbound HTTP POST reaching
# DeployCore, which only ever runs if a human logs in at the console -
# no auto-logon mechanism has survived being tried on real hardware (see
# _specialize_enable_winrm.xml.j2's comment), so in the common,
# unattended case this callback never fires at all, by design, not as a
# failure. This WinRM-reachability check is what actually carries most
# deployments now: _specialize_enable_winrm.xml.j2 gets WinRM listening
# during the specialize pass itself, well before oobeSystem, so a guest
# answering over WinRM is normally the very first real signal Setup has
# finished, not a rare fallback for an occasional network gap anymore.
# Checked more often than that reasoning alone might suggest, but still
# not every single poll: during the (usually much longer) WinPE/Setup
# phase before the specialize pass has even run yet, this would only
# ever return False, no reason to hit the hypervisor API and attempt a
# guest connection on literally every 15-second tick for nothing.
FALLBACK_REACHABILITY_POLL_EVERY = 2  # ~30 seconds at CALLBACK_POLL_INTERVAL_SECONDS

# Hard ceiling on a single WinRM reachability probe (_winrm_reachable
# below). pywinrm has documented, long-standing gaps in its own timeout
# handling (diyan/pywinrm#123, #274, #91 - operations and even the
# initial connection can hang well past whatever read_timeout_sec is
# configured, especially against a host with nothing listening on 5985
# yet, exactly the normal state for most of a reachability-polling
# loop's life, before FirstLogonCommands has run or after a reboot).
# Without this, a single slow/hanging probe stalls whichever loop is
# calling it for as long as pywinrm takes to give up, if it ever does -
# for wait_for_callback's fallback specifically, that meant never
# noticing a real callback landing while stuck on one probe, worse than
# the network gap the fallback exists to work around in the first
# place; for run_post_install's own reachability loops, it meant a
# single stuck attempt silently defeating WINRM_REACHABILITY_MAX_ATTEMPTS
# entirely instead of actually giving up after that many tries.
# asyncio.to_thread + wait_for is what actually makes this enforceable:
# a bare blocking call awaited directly can't be timed out at all once
# started.
WINRM_CHECK_TIMEOUT_SECONDS = 10

# How often a long-running post-install WinRM operation (a Windows
# feature/role install, an app install, a post-install script, a domain
# join) logs a "still running" heartbeat. Real feature installs can
# legitimately take several minutes (Install-WindowsFeature
# AD-Domain-Services, for instance); without a heartbeat the deployment
# log goes completely silent the instant the command starts and stays
# silent until it finishes, indistinguishable from a hang - confirmed as
# a real point of confusion on an otherwise-successful deployment.
HEARTBEAT_INTERVAL_SECONDS = 30

# Phase 1: dismiss the "Press any key to boot from CD or DVD..." prompt,
# which shows up once, early (around when set_boot_order's own 5s
# bootDelay elapses, plus a couple seconds for the loader itself to read
# from the CD), and has a short timeout of its own. Tight interval, short
# window, sized to bracket that one moment, not to keep going until
# Setup's GUI is up (confirmed: a version of this that ran too long once
# landed on and activated the "Support" link's error dialog).
BOOT_KEYPRESS_ATTEMPTS = 15
BOOT_KEYPRESS_INTERVAL_SECONDS = 1

# Phase 2: even with a correctly delivered and applied answer file
# (confirmed: disk partitioning, install, specialize, and oobeSystem all
# run fully unattended once past this), Windows Setup's very first
# interactive screen, choose install language/time-currency
# format/keyboard, is shown unconditionally regardless. Its values are
# already correct (from the answer file, or the install media's own
# single language), so one Enter just advances past it. Timing here
# varies more than phase 1 (however long WinPE takes to finish loading
# its GUI), so this phase is sparser, not to avoid missing it, but to
# avoid repeated blind Enters landing on whatever real dialog Setup has
# moved on to once this screen's already been dismissed. Kept to a
# 45-second window (60s combined with phase 1's own 15s) - explicitly
# requested to be short rather than the "couple of minutes" this used
# to run for, on the theory that WinPE's GUI is reliably up well within
# that window on real hardware/hypervisors this targets.
LANGUAGE_SCREEN_KEYPRESS_ATTEMPTS = 9
LANGUAGE_SCREEN_KEYPRESS_INTERVAL_SECONDS = 5

WINRM_REACHABILITY_POLL_INTERVAL_SECONDS = 10
WINRM_REACHABILITY_MAX_ATTEMPTS = 60  # ~10 minutes

# The Remote Management agent's real work (install DeployCoreAgent.exe,
# enable RDP, create its service, enroll) runs detached from the msiexec call
# that installs the thin wrapper .msi - see remote-agent/wix's own comments
# for why (a nested msiexec inside a custom action deadlocks the Windows
# Installer mutex). That means install_app's own "installed" signal fires as
# soon as the wrapper registers itself, well before the agent has actually
# finished - and this pipeline's own unconditional final reboot (a few steps
# below) doesn't know to wait, so it can - and did, confirmed live - kill the
# agent's background work mid-flight. This poll makes the pipeline actually
# wait for the real completion signal (ManagedHost.enrolled, set by the
# agent's own enrollment call) before moving on, so deployments only reach
# the reboot once Remote Management is genuinely done, not just registered.
REMOTE_AGENT_ENROLL_POLL_INTERVAL_SECONDS = 10
REMOTE_AGENT_ENROLL_MAX_ATTEMPTS = 30  # ~5 minutes


async def _get_virtio_iso(db, org_id: uuid.UUID) -> IsoAsset | None:
    result = await db.execute(
        select(IsoAsset).where(
            IsoAsset.kind == IsoKind.VIRTIO_ISO,
            or_(IsoAsset.org_id == org_id, IsoAsset.org_id.is_(None)),
        )
    )
    return result.scalars().first()


async def _get_or_create_managed_host(db, deployment: Deployment) -> ManagedHost:
    """The one seeded, global is_remote_agent AppAsset (see AppAsset's own
    field docstring) is otherwise identical for every deployment - this is
    what makes each deployment's agent enroll as its own distinct
    ManagedHost rather than all sharing one enroll_token. Looked up by
    deployment_id first, not created unconditionally: run_post_install can
    legitimately run more than once for the same deployment (a redelivered
    arq job, or an operator's "Retry post-install" - see this module's own
    docstrings above), and re-running the app-install loop must reuse the
    same host record and enroll_token rather than orphaning a fresh one
    every time."""
    result = await db.execute(select(ManagedHost).where(ManagedHost.deployment_id == deployment.id))
    host = result.scalars().first()
    if host is not None:
        return host
    host = ManagedHost(
        org_id=deployment.org_id, deployment_id=deployment.id, name=deployment.hostname,
        created_by_user_id=deployment.created_by_user_id,
    )
    db.add(host)
    await db.flush()
    return host


async def _cleanup_answer_floppy(driver: HypervisorDriver, deployment: Deployment) -> None:
    if deployment.answer_iso_remote_path:
        try:
            await driver.delete_iso_from_datastore(deployment.answer_iso_remote_path)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        deployment.answer_iso_remote_path = None


async def _eject_install_media(db, driver: HypervisorDriver, deployment: Deployment) -> None:
    """Windows Setup and its FirstLogonCommands (which is what calls back)
    are the only things that ever read the Windows/VirtIO ISOs or the
    answer-file floppy; once the callback has landed, nothing needs any of
    them again; the rest of post-install runs entirely over WinRM. Ejects
    the CD-ROMs and the floppy alike (kept as empty drives - ESXi rejects
    actually removing a floppy device while the VM is still powered on,
    which this always runs while it is, see esxi.py's detach_floppy), and
    deletes the per-deployment answer floppy image from the datastore, the
    same cleanup _fail() already did on the failure path, now also on
    success.
    Best-effort throughout: never worth failing an otherwise-successful
    deployment over leftover media. Each step logs a warning on failure
    rather than silently passing, though - the unconditional "cleaned up"
    line below used to run regardless of whether any of this actually
    succeeded, which made a real detach_floppy failure invisible (the
    device stayed attached, but the log still claimed success)."""
    if deployment.vm_moref:
        for unit in (WINDOWS_ISO_UNIT, VIRTIO_ISO_UNIT):
            try:
                await driver.detach_iso(deployment.vm_moref, unit)
            except Exception as exc:  # noqa: BLE001 - best-effort, but worth knowing about
                await log(
                    db, deployment, deployment.state.value,
                    f"failed to eject ISO unit {unit}: {exc}", level=LogLevel.WARN,
                )
        try:
            await driver.detach_floppy(deployment.vm_moref)
        except Exception as exc:  # noqa: BLE001 - best-effort, but worth knowing about
            await log(
                db, deployment, deployment.state.value,
                f"failed to remove the answer-file floppy device: {exc}", level=LogLevel.WARN,
            )
    await _cleanup_answer_floppy(driver, deployment)
    await log(db, deployment, deployment.state.value, "install media unmounted and cleaned up")


async def _fail(
    ctx, db, driver: HypervisorDriver, deployment: Deployment, message: str, traceback_text: str | None = None
) -> None:
    await log(db, deployment, deployment.state.value, message, level=LogLevel.ERROR)
    if traceback_text:
        # kept as a separate log line (not folded into `message`, which also
        # becomes the short DeploymentStateTransition detail/error_message)
        # so the state history stays readable while the full trace is still
        # one scroll away in the log stream.
        await log(db, deployment, deployment.state.value, traceback_text, level=LogLevel.ERROR)
    await _cleanup_answer_floppy(driver, deployment)
    deployment.app_asset_access_token = None
    if deployment.vm_moref and deployment.state in _KEEPABLE_ON_FAILURE_STATES:
        await log(
            db, deployment, deployment.state.value,
            "Windows was already fully installed and reachable when this failed - "
            "the VM was kept, not deleted. Use \"Retry post-install\" to try again "
            "without a whole new deployment.",
        )
    elif deployment.vm_moref:
        try:
            await driver.delete_vm(deployment.vm_moref)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        deployment.vm_moref = None
    try:
        await _state_machine.transition(db, deployment, DeploymentState.FAILED, detail=message)
    except InvalidTransition:
        pass  # already terminal
    notifications.notify(
        db,
        user_id=deployment.created_by_user_id,
        deployment_id=deployment.id,
        message=f"Deployment {deployment.hostname} failed: {message}",
    )
    await db.commit()
    await notifications.dispatch(
        db, ctx["redis"], user_id=deployment.created_by_user_id, event_type="failed",
        context={"hostname": deployment.hostname, "error": message},
    )
    await webhooks.dispatch(
        db, ctx["redis"], deployment.org_id, "deployment.failed",
        {"deployment_id": str(deployment.id), "hostname": deployment.hostname, "error": message},
    )


async def run_deployment(ctx, deployment_id: str) -> None:
    async with SessionLocal() as db:
        deployment = await db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None:
            return
        host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
        driver = get_driver(host)
        defaults = HYPERVISOR_DEFAULTS[host.type.value]

        # template_id is nullable (ON DELETE SET NULL, see models/deployment.py):
        # an operator can delete a template out from under a deployment that's
        # still pending/retried, so this can legitimately be None here even
        # though it never was at creation time.
        template = await db.get(DeploymentTemplate, deployment.template_id) if deployment.template_id else None
        if template is None:
            await _fail(ctx, db, driver, deployment, "the template this deployment was created from has since been deleted")
            return
        template = resolve_template(template, deployment.overrides)
        disk_layout = await db.get(DiskLayout, template.disk_layout_id)

        if template.iso_asset_id is None:
            await _fail(ctx, db, driver, deployment, "template has no Windows ISO configured")
            return
        windows_iso = await db.get(IsoAsset, template.iso_asset_id)

        current_step = "starting the provisioning pipeline"
        try:
            await _state_machine.transition(db, deployment, DeploymentState.CREATING_VM)

            current_step = "rendering autounattend.xml"
            # Generated once, up front, and used for both the VM's actual
            # NIC (VmSpec.mac_address below, explicitly assigned rather
            # than hypervisor-generated) and the answer file's
            # static-network Identifier (mac_address_dashes in the
            # template context): they have to be the exact same value, and
            # the VM doesn't exist yet at this point to report one back.
            # See hypervisors/defaults.py's generate_mac_address for why
            # this replaced matching on interface alias.
            mac_address = generate_mac_address(host.type.value)
            rendered_xml = render_autounattend(deployment, template, disk_layout, mac_address)
            deployment.rendered_autounattend = rendered_xml

            current_step = "building and uploading the answer-file floppy"
            await log(db, deployment, "creating_vm", f"building answer-file floppy for {deployment.hostname}")
            answer_floppy_remote_path = await build_and_upload_answer_floppy(driver, deployment, rendered_xml)
            deployment.answer_iso_remote_path = answer_floppy_remote_path
            await db.commit()

            current_step = "uploading the Windows ISO to the datastore"
            # Named by the ISO asset's own id, not its filename: stable across
            # every deployment made from this same asset (so a second
            # deployment can skip re-uploading a multi-gigabyte file that's
            # already there), but never collides with a *different* asset
            # that happens to share a filename after one was deleted and a
            # new one uploaded in its place.
            windows_iso_remote_name = f"{windows_iso.id}{Path(windows_iso.filename).suffix or '.iso'}"
            await log(db, deployment, "creating_vm", f"uploading {windows_iso.filename} to the datastore")
            windows_iso_remote_path = await driver.upload_iso_to_datastore(
                windows_iso.storage_path, windows_iso_remote_name, skip_if_exists=True
            )

            current_step = "creating the VM"
            spec = VmSpec(
                name=deployment.hostname,
                cpu_count=template.cpu_count,
                cores_per_socket=template.cores_per_socket,
                ram_mb=template.ram_mb,
                disk_size_gb=template.disk_size_gb,
                disk_provisioning=template.disk_provisioning.value,
                firmware=defaults["firmware"],
                scsi_controller=defaults["scsi_controller"],
                network_name=template.network_name,
                network_adapter_type=template.network_adapter_type.value,
                # Same fallback chain a bare read of any other
                # EffectiveTemplate-backed field gets for free: per-
                # deployment override (Customize installation), else the
                # template's own preferred_datastore, else the host's own
                # default_datastore - no special-casing needed here since
                # preferred_datastore is a real DeploymentTemplate field
                # like any other, not a one-off VmSpec-only concept.
                datastore=template.preferred_datastore or host.default_datastore,
                mac_address=mac_address,
            )
            vm_ref = await driver.create_vm(spec)
            deployment.vm_moref = vm_ref
            await db.commit()
            await log(db, deployment, "creating_vm", f"VM created ({vm_ref})")

            current_step = "attaching the Windows ISO"
            await driver.attach_iso(vm_ref, windows_iso_remote_path, WINDOWS_ISO_UNIT)

            current_step = "attaching the answer-file floppy"
            await driver.attach_floppy(vm_ref, answer_floppy_remote_path)

            if defaults["requires_driver_injection"]:
                current_step = "attaching the VirtIO driver ISO"
                virtio_iso = await _get_virtio_iso(db, host.org_id)
                if virtio_iso is not None:
                    virtio_remote_name = f"{virtio_iso.id}{Path(virtio_iso.filename).suffix or '.iso'}"
                    virtio_remote_path = await driver.upload_iso_to_datastore(
                        virtio_iso.storage_path, virtio_remote_name, skip_if_exists=True
                    )
                    await driver.attach_iso(vm_ref, virtio_remote_path, VIRTIO_ISO_UNIT)

            current_step = "setting the boot order"
            # Disk first, not CD-ROM first: on the very first boot the disk
            # is blank, so firmware fails it and falls through to the
            # CD-ROM within the same boot pass (fast, well within the boot
            # keypress window below), exactly what bootRetryEnabled/
            # bootDelay are already tuned for. Windows Setup writes a
            # bootloader to the disk partway through installation and
            # reboots to continue; with CD-ROM first that reboot lands back
            # in the CD's WinPE instead of continuing on the disk, and
            # Setup throws "the computer was unexpectedly restarted" since
            # it finds itself back in a fresh WinPE with no memory of the
            # in-progress install. Disk-first sidesteps this entirely: once
            # the disk has a bootloader, every later boot goes straight to
            # it and the CD-ROM is never touched again. Documented
            # Packer/vSphere-iso gotcha, not something specific to us.
            await driver.set_boot_order(vm_ref, ["disk", "cdrom"])
            await log(db, deployment, "creating_vm", "VM created, media attached, powering on")

            current_step = "powering on the VM"
            await _state_machine.transition(db, deployment, DeploymentState.BOOTING)
            await driver.power_on(vm_ref)

            # Best-effort, two phases, see the constants' docstrings above.
            # If either window is somehow missed entirely (the VM's own
            # boot-order retry kicks in 10s later for phase 1, see esxi.py
            # set_boot_order), the deployment timeout is what catches it,
            # not worth failing the deployment over.
            for _ in range(BOOT_KEYPRESS_ATTEMPTS):
                await asyncio.sleep(BOOT_KEYPRESS_INTERVAL_SECONDS)
                try:
                    await driver.send_enter_keypress(vm_ref)
                except Exception:  # noqa: BLE001 - best-effort
                    pass

            for _ in range(LANGUAGE_SCREEN_KEYPRESS_ATTEMPTS):
                await asyncio.sleep(LANGUAGE_SCREEN_KEYPRESS_INTERVAL_SECONDS)
                try:
                    await driver.send_enter_keypress(vm_ref)
                except Exception:  # noqa: BLE001 - best-effort
                    pass

            # Everything DeployCore can actively do to get Setup running
            # unattended off the ISO is done by this point; from here it's
            # entirely a black box until the guest calls back from
            # FirstLogonCommands (Setup fully finished, first boot into the
            # installed OS), there's no way to observe WinPE/Setup progress
            # in between. Moving into installing_os here rather than
            # leaving the deployment shown as "booting" for that whole
            # window is the accurate state either way, this just says so
            # instead of waiting for the one signal that arrives only once
            # installation is already over.
            await _state_machine.transition(db, deployment, DeploymentState.INSTALLING_OS)
            await log(db, deployment, "installing_os", "Windows Setup running, awaiting guest OS install callback")
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator via the log/error_message
            await _fail(ctx, db, driver, deployment, f"failed while {current_step}: {exc}", traceback.format_exc())
            return

    await ctx["redis"].enqueue_job("wait_for_callback", deployment_id)


async def _winrm_reachable(client: WinRMClient) -> bool:
    """is_reachable() is synchronous/blocking (see WinRMClient's own
    docstring: every method needs asyncio.to_thread), and pywinrm has
    documented gaps in its own timeout handling (see
    WINRM_CHECK_TIMEOUT_SECONDS) that can make even this hang well past
    any configured read_timeout_sec against a host with nothing
    listening yet. wait_for's timeout is what actually bounds this: the
    awaiting task moves on within WINRM_CHECK_TIMEOUT_SECONDS regardless
    of whether the background thread is still stuck. Any failure
    (unreachable, a timeout, anything else) just means "not reachable
    yet", same as pywinrm's own is_reachable() already treats its
    failures - never raises."""
    try:
        return await asyncio.wait_for(asyncio.to_thread(client.is_reachable), timeout=WINRM_CHECK_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - a stuck or failed check just means "not reachable yet"
        return False


async def _run_with_retry(blocking_call, attempts: int = 3, delay_seconds: int = 15):
    """Runs a blocking WinRMClient call via asyncio.to_thread, retrying a
    few times with a short delay on any exception. Added after a real
    deployment's post-reboot WinRM call - the very first real command
    issued right after _reboot_and_wait already confirmed reachability -
    still failed with WinRMOperationTimeoutError: the reboot that
    follows a Windows Update install can leave the guest busy finishing
    servicing/TrustedInstaller work well past the point basic
    reachability comes back, and a single successful probe isn't strong
    enough evidence the guest can reliably service a new WinRM shell
    yet. Re-raises the last exception if every attempt fails."""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return await asyncio.to_thread(blocking_call)
        except Exception as exc:  # noqa: BLE001 - retried, not swallowed; re-raised below if attempts run out
            last_exc = exc
            if attempt < attempts - 1:
                await asyncio.sleep(delay_seconds)
    raise last_exc  # type: ignore[misc]


async def _run_with_heartbeat(db, deployment: Deployment, stage: str, description: str, blocking_call, progress_check=None):
    """Runs a blocking WinRMClient call (install_features/install_app/
    run_ps/join_domain - see WinRMClient's own docstring: every method is
    blocking, needs asyncio.to_thread) in a background thread, logging a
    "still running" heartbeat every HEARTBEAT_INTERVAL_SECONDS while it
    hasn't finished yet, instead of the deployment log going silent for
    however long the guest actually takes. asyncio.shield is what keeps
    the underlying call running across heartbeat cycles rather than
    getting cancelled the moment any one wait_for window expires - only
    the waiting is bounded, not the call itself, that's the whole point
    of a heartbeat rather than a timeout.

    progress_check, if given, is an additional blocking call (also run via
    asyncio.to_thread, bounded so a stuck query can't stall a heartbeat
    tick) polled alongside each "still running" line - install_features'
    caller uses it for a per-role rundown (see get_feature_install_status)
    since the batched Install-WindowsFeature call itself can't report
    progress mid-flight. Any failure or timeout is swallowed: a missed
    progress line just falls back to the plain heartbeat, same as before
    this existed."""
    task = asyncio.ensure_future(asyncio.to_thread(blocking_call))
    elapsed = 0
    while not task.done():
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=HEARTBEAT_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            elapsed += HEARTBEAT_INTERVAL_SECONDS
            detail = ""
            if progress_check is not None:
                try:
                    progress = await asyncio.wait_for(asyncio.to_thread(progress_check), timeout=WINRM_CHECK_TIMEOUT_SECONDS)
                    detail = f" ({progress})" if progress else ""
                except Exception:  # noqa: BLE001 - best-effort progress line only
                    pass
            await log(db, deployment, stage, f"{description} - still running ({elapsed}s elapsed){detail}")
    return await task


async def _run_post_install_scripts(db, deployment: Deployment, client: WinRMClient, scripts: list[dict]) -> None:
    """Shared by DiskLayout.post_install_scripts and
    DeploymentTemplate.post_install_scripts - same {name, script_text}
    shape, same run-over-WinRM-in-order semantics, only the source
    differs. Stops (raises) on the first failure: unlike app installs,
    these commonly include disk/partition operations where continuing
    past a failed step could make things worse, not just skip a step."""
    for script in scripts:
        await log(db, deployment, "post_install", f"running post-install script {script['name']}")
        result = await _run_with_heartbeat(
            db, deployment, "post_install", f"running post-install script {script['name']}",
            lambda s=script["script_text"]: client.run_ps(s),
        )
        await log(
            db, deployment, "post_install",
            result.stdout or result.stderr,
            level=LogLevel.INFO if result.ok else LogLevel.ERROR,
        )
        if not result.ok:
            raise RuntimeError(f"post-install script {script['name']} failed: {result.stderr}")
        await log(db, deployment, "post_install", f"post-install script {script['name']} completed")


WINRM_SETTLE_CONSECUTIVE_CHECKS = 3
_LAST_BOOT_TIME_PS = "(Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString('o')"


async def _get_boot_time(client: WinRMClient) -> str:
    """Best-effort guest boot timestamp, empty string on any failure.
    Compared before/after a reboot to prove one actually happened,
    rather than trusting "the guest answered a command" alone - a reboot
    command that silently no-ops (confirmed a real failure mode earlier
    this session: Restart-Computer over WinRM did exactly this) would
    otherwise look identical to a genuine one if the guest happened to
    stay reachable the whole time."""
    try:
        result = await asyncio.to_thread(client.run_ps, _LAST_BOOT_TIME_PS)
        return result.stdout.strip() if result.ok else ""
    except Exception:  # noqa: BLE001 - best-effort, missing just means "can't confirm either way"
        return ""


async def _reboot_and_wait(
    db, deployment: Deployment, stage: str, client: WinRMClient, client_factory, failure_message: str
) -> None:
    """Fire-and-forget the restart (the guest tearing down the WinRM
    connection mid-command is expected, not an error) then wait for it to
    come back, using the same bounded reachability check as everywhere
    else. Shared by the post-feature-install reboot (only when at least
    one installed feature actually reported RestartNeeded) and the
    original end-of-post_install reboot, previously duplicated inline.

    Requires WINRM_SETTLE_CONSECUTIVE_CHECKS in a row, not just one:
    confirmed on a real deployment that a single successful reachability
    probe isn't enough evidence the guest is actually settled - the
    reboot right after a Windows Update install (which can leave the OS
    busy finishing servicing/TrustedInstaller work well past the point
    basic WinRM connectivity comes back) answered one simple command
    fine, then failed opening a new shell for the very next real
    command moments later. One flaky check resets the counter rather
    than failing outright, since a single missed poll this early is
    expected, not a reason to give up.

    client_factory builds a brand-new WinRMClient for every reachability
    check, not the same pre-reboot `client` reused throughout: a real
    reboot severs the underlying TCP connection abruptly, and pooled
    HTTP connections (pywinrm's transport is backed by requests/urllib3)
    can end up stuck reusing a half-dead socket rather than opening a
    fresh one - a plausible, concrete explanation for "the guest is
    genuinely reachable by every other means, but this check never
    agrees," rather than something to just wait out for longer.

    Confirms with the guest's own LastBootUpTime, not just reachability,
    that a reboot actually happened at all, and logs both timestamps -
    directly answering "did it actually reboot" instead of leaving it to
    guesswork or console-watching."""
    boot_time_before = await _get_boot_time(client)
    try:
        client.reboot()
    except Exception:  # noqa: BLE001 - expected: the guest tearing down the WinRM connection mid-reboot
        pass
    await _wait_for_guest_settled(db, deployment, stage, client_factory, boot_time_before, failure_message)


async def _wait_for_guest_settled(
    db, deployment: Deployment, stage: str, client_factory, boot_time_before: str, failure_message: str
) -> None:
    """Shared tail of _reboot_and_wait and
    _shutdown_remove_media_and_power_on below: both trigger a restart
    their own way (a guest-initiated shutdown.exe /r, or a full
    hypervisor power cycle), then wait for it to come back the same way
    - WINRM_SETTLE_CONSECUTIVE_CHECKS in a row, not just one (see
    _reboot_and_wait's own docstring for why), each against a brand-new
    WinRMClient (a real reboot severs the underlying TCP connection
    abruptly, and pooled HTTP connections can get stuck reusing a
    half-dead socket rather than opening a fresh one), then confirmed
    against the guest's own LastBootUpTime, not just reachability, that
    a restart actually happened at all."""
    await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
    consecutive_ok = 0
    for _ in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
        fresh_client = client_factory()
        if await _winrm_reachable(fresh_client):
            consecutive_ok += 1
            if consecutive_ok >= WINRM_SETTLE_CONSECUTIVE_CHECKS:
                boot_time_after = await _get_boot_time(client_factory())
                if boot_time_before and boot_time_after:
                    if boot_time_before == boot_time_after:
                        await log(
                            db, deployment, stage,
                            f"guest is reachable again, but its boot time didn't change "
                            f"({boot_time_after}) - the reboot command may not have actually restarted it",
                            level=LogLevel.WARN,
                        )
                    else:
                        await log(
                            db, deployment, stage,
                            f"guest rebooted and is back: boot time went from {boot_time_before} to {boot_time_after}",
                        )
                else:
                    await log(
                        db, deployment, stage,
                        "guest is reachable again (could not confirm boot time before and/or after to prove "
                        "a reboot actually happened)",
                        level=LogLevel.WARN,
                    )
                return
        else:
            consecutive_ok = 0
        await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
    raise RuntimeError(failure_message)


SHUTDOWN_POLL_INTERVAL_SECONDS = 5
SHUTDOWN_MAX_ATTEMPTS = 24  # ~2 minutes - a clean Windows shutdown is fast, well short of this


async def _shutdown_remove_media_and_power_on(
    db, deployment: Deployment, stage: str, driver: HypervisorDriver, client: WinRMClient, client_factory,
    failure_message: str, cdrom_units: set[int] = frozenset(),
) -> None:
    """Piggybacks the floppy device's (and any given CD-ROM devices')
    one-time removal onto a reboot that's already happening anyway (the
    VMware Tools driver install right before this needs one regardless,
    see run_post_install) - HypervisorDriver.remove_floppy/remove_cdrom
    only work while the VM is genuinely powered off (confirmed against
    the vSphere API's own InvalidPowerState/not_allowed_in_current_state
    requirement), unlike every other reboot in this pipeline, which is a
    guest-initiated restart that never actually reaches poweredOff at
    the hypervisor level. cdrom_units is normally just whichever unit
    mount_tools_installer reported (see run_post_install) - detach_iso
    alone (ejecting, not removing) was all that ever ran for it before,
    leaving an empty-but-present CD-ROM device on every completed
    deployment indefinitely, confirmed live. shutdown.exe /s, not /r:
    same reasoning as WinRMClient.reboot's own choice of shutdown.exe
    over Restart-Computer, just powering off instead of restarting.

    Best-effort throughout, same as everything else around VMware Tools:
    if the guest never actually reaches poweredOff in time, it's powered
    back on regardless rather than left off indefinitely - the floppy/
    CD-ROM devices just don't get removed this run, no worse than
    before this existed. If an individual removal fails, that's logged
    and moved past too - each device was already harmless (ejected,
    empty) either way, removing it outright is a nice-to-have, not
    something worth stalling a deployment over."""
    boot_time_before = await _get_boot_time(client)
    try:
        client.shutdown()
    except Exception:  # noqa: BLE001 - expected: the guest tearing down the WinRM connection mid-shutdown
        pass

    reached_poweroff = False
    for _ in range(SHUTDOWN_MAX_ATTEMPTS):
        if await driver.get_power_state(deployment.vm_moref) == PowerState.POWERED_OFF:
            reached_poweroff = True
            break
        await asyncio.sleep(SHUTDOWN_POLL_INTERVAL_SECONDS)

    if not reached_poweroff:
        await log(
            db, deployment, stage,
            "guest did not reach powered-off in time, continuing without removing the floppy/CD-ROM devices",
            level=LogLevel.WARN,
        )
    else:
        try:
            await driver.remove_floppy(deployment.vm_moref)
            await log(db, deployment, stage, "floppy device removed")
        except Exception as exc:  # noqa: BLE001 - best-effort, never worth failing a deployment over
            await log(
                db, deployment, stage, f"failed to remove the floppy device: {exc}", level=LogLevel.WARN,
            )
        for unit in cdrom_units:
            try:
                await driver.remove_cdrom(deployment.vm_moref, unit)
                await log(db, deployment, stage, f"CD-ROM device (unit {unit}) removed")
            except Exception as exc:  # noqa: BLE001 - best-effort, never worth failing a deployment over
                await log(
                    db, deployment, stage, f"failed to remove CD-ROM device (unit {unit}): {exc}",
                    level=LogLevel.WARN,
                )

    # Only power on if it's not already on: if the shutdown never
    # actually took (reached_poweroff False almost always means this,
    # rather than a still-shutting-down VM this poll interval would
    # have caught), PowerOnVM_Task on an already-running VM raises
    # vim.fault.InvalidPowerState - checked explicitly rather than
    # letting that turn a harmless "shutdown didn't happen" into a
    # failed deployment. _wait_for_guest_settled still runs either way:
    # if the guest was never actually down, it'll be reachable almost
    # immediately and its own boot-time comparison already reports that
    # correctly (a WARN, not a false "rebooted" claim).
    if await driver.get_power_state(deployment.vm_moref) != PowerState.POWERED_ON:
        await driver.power_on(deployment.vm_moref)
    await _wait_for_guest_settled(db, deployment, stage, client_factory, boot_time_before, failure_message)


async def _guest_reachable_over_winrm(
    driver: HypervisorDriver, template: DeploymentTemplate, deployment: Deployment
) -> bool:
    """Best-effort fallback signal for wait_for_callback: WinRM is enabled
    by the same FirstLogonCommands batch that sends the callback, and
    always ordered before it, so a guest answering over WinRM is strong
    evidence Setup finished even if the callback's own outbound POST
    never got through. Any failure here (no guest IP yet, unreachable,
    a hypervisor API hiccup) just means "not yet", same as the normal
    callback wait - this is a backup path, not the primary signal, and
    should never itself raise or fail the deployment.

    A static deployment's IP is already known outright (it's declarative,
    set in the answer file, not learned from anything) - use it directly.
    A DHCP deployment's guest_reported_ip may already be set too, from
    the specialize-pass network ping (_specialize_network_ping.xml.j2,
    api/routes/callback.py's network-ping route) - that only ever reports
    "the guest can reach DeployCore and this is its current address", not
    "Setup is done" (conflating those broke this exact fallback's own
    guarantee on a real deployment, see the network-ping route's own
    docstring), so using it here is safe: this function still only ever
    returns True once WinRM is genuinely, actually reachable, the ping
    just means there's an address worth trying well before VMware Tools
    would ever report one. Only fall through to driver.get_guest_ip() -
    which depends on Tools being installed to report anything at all -
    if neither of those is available yet."""
    try:
        guest_ip = deployment.static_ip if deployment.ip_mode == IpMode.STATIC else deployment.guest_reported_ip
        if not guest_ip:
            guest_ip = await driver.get_guest_ip(deployment.vm_moref)
        if not guest_ip:
            return False
        client = WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password)
        return await _winrm_reachable(client)
    except Exception:  # noqa: BLE001 - best-effort fallback signal, any failure just means "not yet"
        return False


async def wait_for_callback(ctx, deployment_id: str) -> None:
    async with SessionLocal() as db:
        deployment = await db.get(Deployment, uuid.UUID(deployment_id))
        # Stricter than just excluding the terminal states: this job's own
        # only valid precondition is installing_os (set by run_deployment
        # right before enqueueing it, the one and only place that does).
        # arq's at-least-once delivery means this same job can be
        # redelivered and re-run from scratch while the original execution
        # is still alive - confirmed live, a redelivered execution ran this
        # entire function a second time well after the first one had
        # already advanced the deployment into post_install, logging a
        # duplicate "install callback did not arrive" and ultimately
        # crashing run_post_install trying to transition post_install into
        # itself. Bailing here, at the top, before any of that duplicate
        # work happens, is the actual fix - run_post_install has its own
        # matching guard too, as defense in depth for it being called
        # directly (the retry-post-install route enqueues it on its own,
        # not through here).
        if deployment is None or deployment.state != DeploymentState.INSTALLING_OS:
            return

        host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
        driver = get_driver(host)
        # Only needed for the WinRM-reachability fallback below; the
        # callback path itself never touches it. None is a legitimate
        # state (template deleted mid-install, see run_deployment's
        # identical check) - the fallback just can't run without
        # credentials to connect with, same as everywhere else it's used.
        template = await db.get(DeploymentTemplate, deployment.template_id) if deployment.template_id else None
        template = resolve_template(template, deployment.overrides)

        # Deployment is already in installing_os by the time this job is
        # enqueued (run_deployment sets that itself, see above), so state
        # can't be used to detect whether the callback already landed the
        # way it used to; callback_token_used is the actual signal, the
        # callback route sets it and nothing else does. If it's already
        # true, the guest called back before this job even started polling
        # (the race is real, the callback route and this job both react to
        # the same VM boot), skip straight to eject + post-install.
        if not deployment.callback_token_used:
            timeout_minutes = await settings_resolver.resolve(
                db, "os_install_timeout_minutes", org_id=deployment.org_id, template_id=deployment.template_id
            )
            deadline = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)

            poll_count = 0
            fell_back = False
            while datetime.now(timezone.utc) < deadline:
                await db.refresh(deployment)
                if deployment.callback_token_used:
                    break
                if deployment.state == DeploymentState.FAILED:
                    return  # failed elsewhere (e.g. the stale-deployment sweep) while this was polling

                poll_count += 1
                if template is not None and poll_count % FALLBACK_REACHABILITY_POLL_EVERY == 0:
                    if await _guest_reachable_over_winrm(driver, template, deployment):
                        fell_back = True
                        break

                await asyncio.sleep(CALLBACK_POLL_INTERVAL_SECONDS)
            else:
                await _fail(ctx, db, driver, deployment, "timed out waiting for the guest OS install callback")
                return

            if fell_back:
                # Likely a network path issue between the guest and
                # DeployCore rather than a failed install - FirstLogonCommands'
                # callback request just never landed, but its WinRM-enable
                # step (same batch) evidently did.
                await log(
                    db, deployment, "installing_os",
                    "install callback did not arrive, but the guest is reachable over WinRM - "
                    "treating Setup as complete",
                )

        # The callback only ever fires from FirstLogonCommands, i.e. Setup
        # is fully done and the guest has booted its installed OS, the one
        # point we can be sure the install media is safe to unmount. (Or,
        # per the fallback above, the guest answering over WinRM - set up
        # by that same FirstLogonCommands batch - is equally good evidence.)
        await _eject_install_media(db, driver, deployment)

    await run_post_install(ctx, deployment_id)


async def run_post_install(ctx, deployment_id: str) -> None:
    async with SessionLocal() as db:
        deployment = await db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None:
            return
        # arq guarantees at-least-once delivery, not exactly-once - a job
        # can be redelivered and re-run from scratch while the original
        # execution is still alive and working (confirmed live: a second
        # wait_for_callback execution for the same deployment logged its
        # own "install callback did not arrive" a second time, 8 minutes
        # after the first one had already moved the deployment well into
        # post_install, then crashed here trying to transition
        # post_install -> post_install, which the state machine correctly
        # rejects since it's not a real forward transition). Both
        # wait_for_callback (which calls straight into this) and the
        # retry-post-install API route always leave the deployment in
        # exactly installing_os right before this runs - anything else
        # here means a second, stale execution of the same nominal job
        # arrived after the real one already made progress; treat it as
        # a harmless no-op rather than redoing (and crashing on) work
        # that's already done or in progress elsewhere.
        if deployment.state != DeploymentState.INSTALLING_OS:
            await log(
                db, deployment, deployment.state.value,
                "skipping: this looks like a duplicate/redelivered job for a deployment "
                f"already past installing_os (currently {deployment.state.value}) - "
                "another execution already handled it",
                level=LogLevel.WARN,
            )
            return
        host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
        driver = get_driver(host)

        # See run_deployment's identical check: the template can be deleted
        # by an operator at any point while Windows Setup is still running
        # on the guest (wait_for_callback's poll window can be tens of
        # minutes), well after run_deployment itself confirmed it existed.
        template = await db.get(DeploymentTemplate, deployment.template_id) if deployment.template_id else None
        if template is None:
            await _fail(ctx, db, driver, deployment, "the template this deployment was created from has since been deleted")
            return
        template = resolve_template(template, deployment.overrides)
        disk_layout = await db.get(DiskLayout, template.disk_layout_id)

        try:
            # Three sources, in order of how little they depend on
            # anything: a static deployment's IP is already known outright
            # (declarative, set in the specialize pass, live before Setup
            # even finishes - see autounattend_base.xml.j2/
            # _static_network.xml.j2). Otherwise, the callback itself
            # already told us: the guest is, by definition, whoever made
            # that HTTP connection (api/routes/callback.py captures
            # request.client.host the moment it lands). Only fall through
            # to driver.get_guest_ip() - which depends entirely on VMware
            # Tools being installed in the guest to report anything at all
            # - if neither of those is available (a DHCP deployment that
            # advanced via wait_for_callback's WinRM-reachability fallback
            # instead of a real callback landing, so there's no captured
            # address to use). A real deployment got stuck for exactly
            # this reason, an environment without VMware Tools installed
            # spinning here for the full WINRM_REACHABILITY_MAX_ATTEMPTS
            # despite Setup and the callback having both already
            # succeeded.
            guest_ip = deployment.static_ip or deployment.guest_reported_ip
            if not guest_ip:
                await log(db, deployment, "post_install", "waiting for guest IP address")
                for _ in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
                    guest_ip = await driver.get_guest_ip(deployment.vm_moref)
                    if guest_ip:
                        break
                    await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
                if not guest_ip:
                    raise RuntimeError("guest never reported an IP address")

            # Logged immediately, not just on eventual failure: this loop
            # previously ran completely silently for up to
            # WINRM_REACHABILITY_MAX_ATTEMPTS * WINRM_REACHABILITY_POLL_INTERVAL_SECONDS
            # (~10 minutes) with nothing in the deployment log to show for
            # it - indistinguishable from a genuine hang, confirmed
            # confusing on a real deployment that was, in fact, still
            # actively (if silently) retrying. Which guest_ip this is
            # trying matters on its own too: for a DHCP deployment it's
            # deployment.guest_reported_ip (whichever address the guest's
            # own callback request arrived from, see above) - if that's
            # wrong (a NAT/proxy between the guest's network and
            # DeployCore reporting some other source address, or a
            # network segment DeployCore's own host genuinely has no
            # route back into) no amount of retrying here will ever
            # succeed, and this line is what lets an operator actually
            # see which address to go check reachability against instead
            # of guessing.
            await log(db, deployment, "post_install", f"waiting for {guest_ip} to become reachable over WinRM")
            client = WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password)
            re_resolved = False
            for attempt in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
                if await _winrm_reachable(client):
                    break
                # A DHCP deployment's guest_ip normally comes from
                # deployment.guest_reported_ip - whichever address the
                # guest's callback request arrived from, captured early
                # (the specialize-pass callback fires well before Setup's
                # own final reboot into the running OS, see above). DHCP
                # *usually* renews the same lease/address across a reboot
                # on the same adapter, but that's not guaranteed - if the
                # guest genuinely came back on a different address, no
                # amount of retrying the stale one would ever succeed.
                # One-shot cross-check partway through the window (not
                # every attempt - driver.get_guest_ip is a real
                # hypervisor API call, worth paying for once, not 60
                # times): if the hypervisor's own view disagrees, switch
                # to it and keep going on the remaining attempts instead
                # of burning the rest of the budget on an address that
                # may no longer mean anything.
                if not re_resolved and attempt == WINRM_REACHABILITY_MAX_ATTEMPTS // 3:
                    re_resolved = True
                    try:
                        current_ip = await driver.get_guest_ip(deployment.vm_moref)
                    except Exception:  # noqa: BLE001 - best-effort cross-check, the original guest_ip is still tried either way
                        current_ip = None
                    if current_ip and current_ip != guest_ip:
                        await log(
                            db, deployment, "post_install",
                            f"guest now reports a different address ({current_ip}, was {guest_ip}) - "
                            "switching to it",
                        )
                        guest_ip = current_ip
                        client = WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password)
                        if deployment.ip_mode != IpMode.STATIC:
                            # Persisted so a subsequent "Retry post-install"
                            # (which reads guest_reported_ip fresh, same as
                            # this run did) starts from the corrected
                            # address instead of the same stale one.
                            deployment.guest_reported_ip = current_ip
                            await db.commit()
                elif attempt > 0 and attempt % 3 == 0:
                    await log(
                        db, deployment, "post_install",
                        f"still waiting for {guest_ip} to become reachable over WinRM "
                        f"({attempt * WINRM_REACHABILITY_POLL_INTERVAL_SECONDS}s elapsed)",
                    )
                await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
            else:
                raise RuntimeError(f"guest at {guest_ip} never became reachable over WinRM")

            await _state_machine.transition(db, deployment, DeploymentState.POST_INSTALL)

            # Disk layout's own post-install scripts run before literally
            # everything else, including VMware Tools: these exist for
            # disk/partition fixups (diskpart, DISM, reagentc, bcdedit -
            # see DiskLayout.post_install_scripts) that need a pristine,
            # freshly-booted disk, before anything else (a Tools driver
            # update and its reboot, a feature install) touches it.
            await _run_post_install_scripts(db, deployment, client, disk_layout.post_install_scripts)

            # First post-install step after that, ahead of roles/features/apps: makes
            # the static-IP cross-check below (and any future use of
            # driver.get_guest_ip) actually have something to report, and
            # gets VMware's own driver/integration updates in place before
            # anything else runs on top of them. See WinRMClient.
            # install_vmware_tools for why this runs here over WinRM
            # instead of during Windows Setup's specialize pass.
            if template.install_vmware_tools:
                # mount_tools_installer runs right here, not at VM creation -
                # see its own docstring (esxi.py) for why: it needs an
                # existing CD-ROM device to attach the Tools ISO to, and by
                # now the Windows ISO's device already exists and has already
                # been ejected (empty) by the Setup-complete callback handler,
                # free to reuse. Best-effort, same as everything else here -
                # if this genuinely isn't an ESXi host, or the mount call
                # itself fails, install_vmware_tools below just won't find
                # anything on D:/E:/F: and reports NOT_FOUND the same as
                # always.
                tools_cdrom_unit = None
                try:
                    tools_cdrom_unit = await driver.mount_tools_installer(deployment.vm_moref)
                except Exception as exc:  # noqa: BLE001 - best-effort, never worth failing a deployment over
                    await log(
                        db, deployment, "post_install",
                        f"failed to mount VMware Tools installer: {exc}", level=LogLevel.WARN,
                    )

                await log(db, deployment, "post_install", "installing VMware Tools")
                tools_result = await _run_with_heartbeat(
                    db, deployment, "post_install", "installing VMware Tools",
                    client.install_vmware_tools,
                )
                if not tools_result.ok:
                    await log(
                        db, deployment, "post_install",
                        f"VMware Tools install failed, continuing without it: {tools_result.stderr}",
                        level=LogLevel.WARN,
                    )
                elif tools_result.installed:
                    await log(
                        db, deployment, "post_install",
                        "VMware Tools installed, shutting down to apply driver updates and remove leftover media devices",
                    )
                    # Removes the floppy and the CD-ROM device
                    # mount_tools_installer used, not just ejects them -
                    # the one point in the pipeline the VM is genuinely
                    # powered off, since InvalidPowerState rules it out
                    # everywhere else. Nothing left to eject below once
                    # this runs: set to None so the fallback further down
                    # (for the "mounted but never actually installed"
                    # case, which never reaches a powered-off VM) doesn't
                    # try a second time.
                    await _shutdown_remove_media_and_power_on(
                        db, deployment, "post_install", driver, client,
                        lambda: WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password),
                        "guest did not come back reachable after the VMware Tools reboot",
                        cdrom_units={tools_cdrom_unit} if tools_cdrom_unit is not None else set(),
                    )
                    tools_cdrom_unit = None
                else:
                    await log(db, deployment, "post_install", "no VMware Tools installer found (not an ESXi host, or Tools ISO wasn't mounted)")

                # Fallback, eject-only cleanup: only reached when the
                # shutdown/full-removal path above never ran (Tools
                # wasn't actually installed this run, or the mount call
                # itself failed) - the VM never reaches powered-off in
                # that case, so full removal isn't possible, but
                # whatever mount_tools_installer did manage to mount is
                # still worth ejecting rather than left mounted
                # indefinitely.
                if tools_cdrom_unit is not None:
                    try:
                        await driver.detach_iso(deployment.vm_moref, tools_cdrom_unit)
                        await log(db, deployment, "post_install", "VMware Tools installer media ejected")
                    except Exception as exc:  # noqa: BLE001 - best-effort, never worth failing a deployment over
                        await log(
                            db, deployment, "post_install",
                            f"failed to eject VMware Tools installer media: {exc}", level=LogLevel.WARN,
                        )
            else:
                await log(db, deployment, "post_install", "skipping VMware Tools (disabled for this template/deployment)")

            if deployment.ip_mode == IpMode.STATIC:
                # Cross-check, not a source of truth: guest_ip above
                # already used deployment.static_ip directly without
                # needing this at all. A mismatch doesn't necessarily mean
                # anything is wrong (a second NIC, a Tools version that
                # hasn't reported yet, ...), it's a WARN, not an error.
                try:
                    reported_ip = await asyncio.wait_for(
                        driver.get_guest_ip(deployment.vm_moref), timeout=WINRM_CHECK_TIMEOUT_SECONDS
                    )
                except Exception:  # noqa: BLE001 - best-effort cross-check only
                    reported_ip = None
                if reported_ip and reported_ip != deployment.static_ip:
                    await log(
                        db, deployment, "post_install",
                        f"guest-reported IP ({reported_ip}) doesn't match the configured static "
                        f"address ({deployment.static_ip}) - worth a look, but not necessarily wrong "
                        "(a second NIC, for instance)",
                        level=LogLevel.WARN,
                    )
                elif reported_ip:
                    await log(
                        db, deployment, "post_install",
                        f"guest-reported IP confirms the configured static address ({reported_ip})",
                    )

            if template.windows_features:
                # One Install-WindowsFeature call for every requested
                # feature together, not one call per feature (see
                # WinRMClient.install_features): a single DISM/CBS
                # transaction, meaningfully faster than N sequential ones,
                # and what Server Manager's own "Add Roles and Features"
                # wizard does. -IncludeManagementTools is always on inside
                # that call, so a GUI edition gets ADUC, Group Policy
                # Management, the DNS/DHCP consoles, etc. alongside
                # whichever roles pull them in, matching what installing
                # through Server Manager's GUI gives you by default.
                await log(
                    db, deployment, "post_install",
                    f"installing Windows features: {', '.join(template.windows_features)}",
                )
                def _feature_progress() -> str:
                    # A second, independent WinRMClient/session, not the
                    # shared `client` install_features is using: NTLM
                    # message signing keeps per-session sequence counters,
                    # and two concurrent requests over the same session
                    # (this progress poll racing the in-flight install)
                    # corrupts them - confirmed live as BadMICError
                    # ("Invalid Message Integrity Check") crashing the
                    # whole install the first time this ran.
                    progress_client = WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password)
                    status = progress_client.get_feature_install_status(template.windows_features)
                    done = [name for name in template.windows_features if status.get(name)]
                    return f"{len(done)}/{len(template.windows_features)} installed so far: {', '.join(done)}" if done else ""

                # ponytail: 0x80070020 (ERROR_SHARING_VIOLATION) right after
                # first boot is a well-known transient servicing-stack lock
                # (TrustedInstaller/CBS still settling), not a real failure -
                # 3 tries, 30s apart, before giving up for real.
                for attempt in range(3):
                    result = await _run_with_heartbeat(
                        db, deployment, "post_install", "installing Windows features",
                        lambda: client.install_features(template.windows_features),
                        progress_check=_feature_progress,
                    )
                    if result.ok or "0x80070020" not in (result.stderr or ""):
                        break
                    await asyncio.sleep(30)
                await log(
                    db,
                    deployment,
                    "post_install",
                    result.stdout or result.stderr,
                    level=LogLevel.INFO if result.ok else LogLevel.ERROR,
                )
                if not result.ok:
                    raise RuntimeError(f"Install-WindowsFeature failed: {result.stderr}")

                await log(db, deployment, "post_install", "verifying installed Windows features")
                verify_result = await _run_with_heartbeat(
                    db, deployment, "post_install", "verifying installed Windows features",
                    lambda: client.verify_windows_features_installed(template.windows_features),
                )
                if not verify_result.ok:
                    raise RuntimeError(f"feature verification failed: {verify_result.stderr}")
                await log(db, deployment, "post_install", "Windows features installed and verified")

                if result.restart_needed:
                    await log(
                        db, deployment, "post_install",
                        "a feature install requires a restart, rebooting before continuing",
                    )
                    await _reboot_and_wait(
                        db, deployment, "post_install", client,
                        lambda: WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password),
                        "guest did not come back reachable after the feature-install reboot",
                    )

            if template.enable_rdp:
                await log(db, deployment, "post_install", "enabling Remote Desktop")
                rdp_result = await _run_with_heartbeat(
                    db, deployment, "post_install", "enabling Remote Desktop",
                    client.enable_rdp,
                )
                if not rdp_result.ok:
                    raise RuntimeError(f"enabling Remote Desktop failed: {rdp_result.stderr}")
                await log(db, deployment, "post_install", "Remote Desktop enabled")

            if template.app_installs:
                # Back to running before post-install scripts and the
                # final reboot, not after it - moved there briefly this
                # session on the reasoning that a settled, fully-patched
                # guest is a more representative install target, but that
                # meant an app needing its own reboot to finish never got
                # one (the final reboot only fires once, before app
                # installs ran, not after) - a real regression, reverted.
                # A post-install script can once again assume an app
                # installed earlier in this list is already present by
                # the time it runs.
                #
                # Short-lived, cleared right after this block: authenticates
                # the guest's own Invoke-WebRequest calls back to DeployCore
                # for each app it downloads (see api/routes/app_assets.py's
                # download_app_asset), there's no user session to
                # authenticate those with, same reasoning as callback_token.
                deployment.app_asset_access_token = secrets.token_urlsafe(32)
                await db.commit()
                callback_base_url = get_settings().app_public_url
                for entry in template.app_installs:
                    app_asset = await db.get(AppAsset, uuid.UUID(entry["app_asset_id"]))
                    if app_asset is None:
                        await log(
                            db, deployment, "post_install",
                            f"skipping app install: asset {entry['app_asset_id']} no longer exists",
                            level=LogLevel.ERROR,
                        )
                        continue
                    install_args = entry.get("install_args") or app_asset.default_install_args
                    if app_asset.is_remote_agent:
                        # The agent installer is one seeded, global asset,
                        # identical for every deployment (see AppAsset.
                        # is_remote_agent's own docstring) - template-level
                        # install_args is static and can't carry values
                        # unique to this one deployment, so the DeployCore
                        # URL and this host's own enroll token are appended
                        # here instead, the one point in the pipeline that's
                        # actually deployment-specific. These are MSI
                        # properties (NAME=value, consumed by the agent
                        # .msi's install custom action - see
                        # remote-agent/wix/) - the same SERVERURL/ENROLLTOKEN
                        # the Remote Management page's copyable install
                        # command passes for standalone (non-deployed) hosts.
                        managed_host = await _get_or_create_managed_host(db, deployment)
                        await db.commit()
                        if managed_host.enrolled:
                            # A "Retry post-install" after the agent actually
                            # finished enrolling on its own in the background
                            # since the last attempt failed - it keeps
                            # retrying independently (ONSTART-resume, see the
                            # agent script's own docstring), so this can
                            # legitimately already be done by the time an
                            # operator retries. Nothing left to do; re-running
                            # the installer would just be redundant.
                            await log(
                                db, deployment, "post_install",
                                "Remote Management agent already enrolled",
                            )
                            continue
                        server_url = await remote_session.resolve_app_public_url(db)
                        install_args = (
                            f'{install_args} SERVERURL="{server_url}" ENROLLTOKEN={managed_host.enroll_token}'.strip()
                        )
                    await log(db, deployment, "post_install", f"installing {app_asset.name}")
                    download_url = (
                        f"{callback_base_url}/api/deployments/{deployment.id}/app-assets/{app_asset.id}/download"
                        f"?token={deployment.app_asset_access_token}"
                    )
                    remote_path = f"C:\\Windows\\Temp\\{app_asset.id}-{app_asset.filename}"
                    result = await _run_with_heartbeat(
                        db, deployment, "post_install", f"installing {app_asset.name}",
                        lambda u=download_url, p=remote_path, k=app_asset.kind.value, a=install_args:
                            client.install_app(u, p, k, a),
                    )
                    await log(
                        db,
                        deployment,
                        "post_install",
                        result.stdout or result.stderr,
                        level=LogLevel.INFO if result.ok else LogLevel.ERROR,
                    )
                    if not result.ok:
                        raise RuntimeError(f"installing {app_asset.name} failed (exit {result.status_code}): {result.stderr}")
                    await log(db, deployment, "post_install", f"{app_asset.name} installed")

                    if app_asset.is_remote_agent:
                        # See REMOTE_AGENT_ENROLL_MAX_ATTEMPTS's own comment:
                        # "installed" above only means the wrapper .msi
                        # registered itself - the agent's actual work is
                        # still running in the background at this point.
                        #
                        # Deliberately fatal, not best-effort: an earlier
                        # version of this just logged a WARN and let the
                        # deployment continue to "completed" - reasoning that
                        # Remote Management is an add-on, not core to "this VM
                        # deployed successfully". In practice that made a
                        # genuinely broken agent install (wrong server
                        # address, a firewall blocking it, a bug in the
                        # installer) invisible - deployments always showed
                        # "completed" even when Remote Management would in
                        # fact never come up, with no obvious way to retry
                        # just that part. Raising here instead fails the
                        # deployment from POST_INSTALL (a state
                        # _KEEPABLE_ON_FAILURE_STATES preserves the VM for),
                        # which surfaces the problem clearly and makes "Retry
                        # post-install" available - and safe to use: the
                        # managed_host.enrolled check above means a retry
                        # after the agent's own background retry (it keeps
                        # trying independently, surviving reboots) already
                        # succeeded just skips straight past this.
                        await log(db, deployment, "post_install", "waiting for the Remote Management agent to finish enrolling...")
                        enrolled = False
                        for _ in range(REMOTE_AGENT_ENROLL_MAX_ATTEMPTS):
                            await db.refresh(managed_host)
                            if managed_host.enrolled:
                                enrolled = True
                                break
                            await asyncio.sleep(REMOTE_AGENT_ENROLL_POLL_INTERVAL_SECONDS)
                        if enrolled:
                            await log(db, deployment, "post_install", "Remote Management agent enrolled")
                        else:
                            raise RuntimeError(
                                "Remote Management agent did not finish enrolling within the wait window "
                                f"({REMOTE_AGENT_ENROLL_MAX_ATTEMPTS * REMOTE_AGENT_ENROLL_POLL_INTERVAL_SECONDS}s). "
                                "Check the Instance URL in Settings -> Remote Management is reachable from this VM, "
                                "then use \"Retry post-install\" once fixed - the agent may also still succeed on its "
                                "own in the background, in which case a retry will detect that and skip straight past."
                            )
                deployment.app_asset_access_token = None
                await db.commit()

            await _run_post_install_scripts(db, deployment, client, template.post_install_scripts)

            await _state_machine.transition(db, deployment, DeploymentState.CONFIGURING)

            # Best-effort, deliberately last before the final reboot below
            # (which already happens unconditionally, so no separate
            # reboot-if-needed tracking is needed here): a VM built from a
            # months-old ISO can be meaningfully behind on day one, this
            # catches it up in the same pass. Never fails the deployment -
            # an update server hiccup is a WARN, not a reason to mark an
            # otherwise-successful deployment failed.
            def _update_progress() -> str:
                # Separate WinRMClient/session, not the one running the
                # install - sharing one session corrupts NTLM's own
                # message-signing state, same lesson learned the hard
                # way with the feature-install progress check above.
                progress_client = WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password)
                return progress_client.get_windows_update_progress()

            if template.install_windows_updates:
                await log(db, deployment, "configuring", "checking for Windows updates")
                update_result = await _run_with_heartbeat(
                    db, deployment, "configuring", "checking for Windows updates",
                    client.install_windows_updates,
                    progress_check=_update_progress,
                )
                await log(
                    db, deployment, "configuring",
                    update_result.stdout or update_result.stderr,
                    level=LogLevel.INFO if update_result.ok else LogLevel.WARN,
                )
            else:
                await log(db, deployment, "configuring", "skipping Windows Update (disabled for this template/deployment)")

            # After software installs and Windows Update, not before: a
            # domain controller's own policies (or just its being on the
            # network at all) can affect what "finished installing" even
            # means for some apps/updates, so join last, right before the
            # single final reboot that picks up domain membership anyway.
            if template.domain_join_enabled and template.domain_join_timing == DomainJoinTiming.POST_INSTALL:
                await log(db, deployment, "configuring", f"joining domain {template.domain_fqdn}")
                domain_result = await _run_with_heartbeat(
                    db, deployment, "configuring", f"joining domain {template.domain_fqdn}",
                    lambda: client.join_domain(
                        template.domain_fqdn, template.domain_join_account, template.domain_join_credential,
                        template.domain_target_ou,
                    ),
                )
                if not domain_result.ok:
                    raise RuntimeError(f"domain join failed: {domain_result.stderr}")
                await log(db, deployment, "configuring", f"joined domain {template.domain_fqdn}")

            await log(db, deployment, "configuring", "rebooting to finalize configuration")
            await _reboot_and_wait(
                db, deployment, "configuring", client,
                lambda: WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password),
                "guest did not come back reachable after the post-install reboot",
            )

            if template.custom_admin_enabled:
                # Deliberately this late, not in FirstLogonCommands
                # (_first_logon_commands.xml.j2 still has a
                # defense-in-depth copy of this for the rare case someone
                # does log in manually, but FirstLogonCommands only ever
                # runs on an actual interactive logon - which nothing in
                # the normal unattended flow ever does now that WinRM
                # access comes from the specialize pass instead of
                # AutoLogon, so it was never actually running in
                # practice). Doing it here, over WinRM, guarantees it
                # actually happens, and only after every other
                # post-install step (roles, apps, scripts, updates) has
                # already run - some of those could plausibly still
                # depend on Administrator being enabled.
                await log(db, deployment, "configuring", "disabling built-in Administrator account")
                disable_result = await _run_with_retry(client.disable_builtin_administrator)
                await log(
                    db, deployment, "configuring",
                    disable_result.stdout or disable_result.stderr,
                    level=LogLevel.INFO if disable_result.ok else LogLevel.WARN,
                )

            # Three things need cleaning up here, not just the one custom
            # rule - confirmed against Microsoft's own Disable-PSRemoting
            # docs, which explicitly list the other two as manual steps
            # it does NOT do on its own:
            #
            # 1. The "DeployCore WinRM" rule: created via netsh
            #    (_specialize_enable_winrm.xml.j2), and a real deployment
            #    showed it silently still present and enabled afterward -
            #    PowerShell's own NetSecurity cmdlets querying by
            #    DisplayName aren't guaranteed to match a rule netsh
            #    created, so delete it with the same tool that made it.
            # 2. The built-in "Windows Remote Management (HTTP-In)" rule:
            #    `winrm quickconfig` (also run in the specialize pass)
            #    enables this pre-shipped-but-disabled-by-default rule as
            #    a separate, documented side effect, distinct from the
            #    custom one above - Disable-PSRemoting's own docs list
            #    "disable the firewall exceptions for WS-Management
            #    communications" as something it explicitly does NOT do.
            #    Disabled by -Name, not -DisplayGroup: "Windows Remote
            #    Management" is a localized display string, same lesson
            #    already learned the hard way for RDP's DisplayGroup.
            #    Restored to its default (present but disabled) state,
            #    not deleted - it's a predefined Windows rule, not one
            #    this project created.
            # 3. LocalAccountTokenFilterPolicy: set to 1 by the
            #    specialize pass when a custom admin account is
            #    configured (see _specialize_enable_winrm.xml.j2), and
            #    also explicitly listed in Disable-PSRemoting's own docs
            #    as a manual-revert step it doesn't do - left at 1 this
            #    weakens UAC's remote-token filtering for every local
            #    admin account on the machine, indefinitely, well past
            #    the WinRM access it was only ever needed for.
            #
            # All four steps run as ONE call, wrapped in a swallow-all
            # try/except, not the netsh/firewall/registry cleanup logged
            # separately followed by a swallowed Disable-PSRemoting: the
            # WINRM-HTTP-In-TCP rule being disabled here is the same rule
            # this very session's own connection is using right now.
            # Windows Firewall's stateful tracking *usually* lets an
            # already-established connection keep flowing after its rule
            # is disabled, but that's not guaranteed on every config - if
            # it doesn't, this command's own response has to travel back
            # over the connection the command itself just cut, which
            # previously meant _run_with_retry burning several minutes
            # retrying (each retry opening a brand-new connection against
            # a now-disabled rule, guaranteed to fail every time) before
            # finally raising and failing an otherwise fully-succeeded
            # deployment right at the last step. Splitting it never
            # protected anything real: the firewall/registry cleanup was
            # exactly as capable of severing the session as
            # Disable-PSRemoting always was.
            await log(db, deployment, "configuring", "closing WinRM access, post-install is finished")
            try:
                await asyncio.to_thread(
                    client.run_ps,
                    'netsh.exe advfirewall firewall delete rule name="DeployCore WinRM"; '
                    "Disable-NetFirewallRule -Name 'WINRM-HTTP-In-TCP','WINRM-HTTP-In-TCP-PUBLIC' "
                    "-ErrorAction SilentlyContinue; "
                    "Remove-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Policies\\System' "
                    "-Name LocalAccountTokenFilterPolicy -ErrorAction SilentlyContinue; "
                    "Disable-PSRemoting -Force; "
                    "Start-Process powershell.exe -WindowStyle Hidden -ArgumentList "
                    "'-NoProfile -Command \"Start-Sleep -Seconds 5; "
                    "Stop-Service WinRM -Force; Set-Service WinRM -StartupType Disabled\"'"
                )
            except Exception:  # noqa: BLE001 - expected: this call may not get to report back before WinRM dies
                pass

            await _cleanup_answer_floppy(driver, deployment)
            await _state_machine.transition(db, deployment, DeploymentState.COMPLETED)
            await log(db, deployment, "completed", "deployment finished successfully")
            notifications.notify(
                db,
                user_id=deployment.created_by_user_id,
                deployment_id=deployment.id,
                message=f"Deployment {deployment.hostname} completed successfully",
            )
            await db.commit()
            await notifications.dispatch(
                db, ctx["redis"], user_id=deployment.created_by_user_id, event_type="complete",
                context={"hostname": deployment.hostname},
            )
            await webhooks.dispatch(
                db, ctx["redis"], deployment.org_id, "deployment.complete",
                {"deployment_id": str(deployment.id), "hostname": deployment.hostname},
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator via the log/error_message
            await _fail(ctx, db, driver, deployment, str(exc), traceback.format_exc())


async def cleanup_deployment(ctx, deployment_id: str, reason: str) -> None:
    """Force-finalizer used by maintenance.sweep_stale_deployments for
    deployments stuck past their stage timeout."""
    async with SessionLocal() as db:
        deployment = await db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None:
            return
        host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
        driver = get_driver(host)
        await _fail(ctx, db, driver, deployment, reason)
