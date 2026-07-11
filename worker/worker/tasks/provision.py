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
from app.hypervisors.base import HypervisorDriver, VmSpec
from app.hypervisors.defaults import HYPERVISOR_DEFAULTS, generate_mac_address
from app.models.app_asset import AppAsset
from app.models.deployment import Deployment, DeploymentState, LogLevel
from app.models.disk_layout import DiskLayout
from app.models.hypervisor import HypervisorHost
from app.models.iso_asset import IsoAsset, IsoKind
from app.models.template import DeploymentTemplate, DomainJoinTiming
from app.services import notifications, settings_resolver, webhooks
from app.services.deployment_service import DeploymentStateMachine, InvalidTransition, log
from app.services.floppy_builder import build_and_upload_answer_floppy
from app.services.template_render import render_autounattend
from app.winrm.client import WinRMClient

_state_machine = DeploymentStateMachine()

WINDOWS_ISO_UNIT = 0
VIRTIO_ISO_UNIT = 1

CALLBACK_POLL_INTERVAL_SECONDS = 15

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
# varies a lot more (however long WinPE takes to finish loading its
# GUI), so this phase is sparser than phase 1, not to avoid missing it,
# but to avoid repeated blind Enters landing on whatever real dialog
# Setup has moved on to once this screen's already been dismissed.
LANGUAGE_SCREEN_KEYPRESS_ATTEMPTS = 12
LANGUAGE_SCREEN_KEYPRESS_INTERVAL_SECONDS = 8

WINRM_REACHABILITY_POLL_INTERVAL_SECONDS = 10
WINRM_REACHABILITY_MAX_ATTEMPTS = 60  # ~10 minutes


async def _get_virtio_iso(db, org_id: uuid.UUID) -> IsoAsset | None:
    result = await db.execute(
        select(IsoAsset).where(
            IsoAsset.kind == IsoKind.VIRTIO_ISO,
            or_(IsoAsset.org_id == org_id, IsoAsset.org_id.is_(None)),
        )
    )
    return result.scalars().first()


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
    the CD-ROMs (kept as empty drives, matching detach_iso's existing
    eject-not-remove behavior), removes the floppy device outright (it has
    no purpose beyond delivering the answer file), and deletes the
    per-deployment answer floppy image from the datastore, the same
    cleanup _fail() already did on the failure path, now also on success.
    Best-effort throughout: never worth failing an otherwise-successful
    deployment over leftover media."""
    if deployment.vm_moref:
        for unit in (WINDOWS_ISO_UNIT, VIRTIO_ISO_UNIT):
            try:
                await driver.detach_iso(deployment.vm_moref, unit)
            except Exception:  # noqa: BLE001 - best-effort
                pass
        try:
            await driver.detach_floppy(deployment.vm_moref)
        except Exception:  # noqa: BLE001 - best-effort
            pass
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
    if deployment.vm_moref:
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
    await notifications.maybe_email(
        db, ctx["redis"], user_id=deployment.created_by_user_id, event_type="failed",
        subject=f"Deployment {deployment.hostname} failed",
        body=f"Deployment {deployment.hostname} failed: {message}",
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
                datastore=host.default_datastore,
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


async def wait_for_callback(ctx, deployment_id: str) -> None:
    async with SessionLocal() as db:
        deployment = await db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None or deployment.state in (DeploymentState.COMPLETED, DeploymentState.FAILED):
            return

        host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
        driver = get_driver(host)

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

            while datetime.now(timezone.utc) < deadline:
                await db.refresh(deployment)
                if deployment.callback_token_used:
                    break
                if deployment.state == DeploymentState.FAILED:
                    return  # failed elsewhere (e.g. the stale-deployment sweep) while this was polling
                await asyncio.sleep(CALLBACK_POLL_INTERVAL_SECONDS)
            else:
                await _fail(ctx, db, driver, deployment, "timed out waiting for the guest OS install callback")
                return

        # The callback only ever fires from FirstLogonCommands, i.e. Setup
        # is fully done and the guest has booted its installed OS, the one
        # point we can be sure the install media is safe to unmount.
        await _eject_install_media(db, driver, deployment)

    await run_post_install(ctx, deployment_id)


async def run_post_install(ctx, deployment_id: str) -> None:
    async with SessionLocal() as db:
        deployment = await db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None:
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

        try:
            # A static deployment's IP is already live at this point (see
            # autounattend_base.xml.j2/_static_network.xml.j2: it's set
            # declaratively in the specialize pass, no DHCP involved at
            # any point), so get_guest_ip reports it directly like it
            # would a DHCP address, one connection is enough either way.
            await log(db, deployment, "post_install", "waiting for guest IP address")
            guest_ip = None
            for _ in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
                guest_ip = await driver.get_guest_ip(deployment.vm_moref)
                if guest_ip:
                    break
                await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
            if not guest_ip:
                raise RuntimeError("guest never reported an IP address")

            client = WinRMClient(guest_ip, template.local_admin_username, template.local_admin_password)
            for _ in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
                if client.is_reachable():
                    break
                await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
            else:
                raise RuntimeError(f"guest at {guest_ip} never became reachable over WinRM")

            await _state_machine.transition(db, deployment, DeploymentState.POST_INSTALL)

            for feature in template.windows_features:
                await log(db, deployment, "post_install", f"installing Windows feature {feature}")
                result = client.install_feature(feature)
                await log(
                    db,
                    deployment,
                    "post_install",
                    result.stdout or result.stderr,
                    level=LogLevel.INFO if result.ok else LogLevel.ERROR,
                )
                if not result.ok:
                    raise RuntimeError(f"Install-WindowsFeature {feature} failed: {result.stderr}")

            if template.app_installs:
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
                    await log(db, deployment, "post_install", f"installing {app_asset.name}")
                    download_url = (
                        f"{callback_base_url}/api/deployments/{deployment.id}/app-assets/{app_asset.id}/download"
                        f"?token={deployment.app_asset_access_token}"
                    )
                    remote_path = f"C:\\Windows\\Temp\\{app_asset.id}-{app_asset.filename}"
                    result = client.install_app(download_url, remote_path, app_asset.kind.value, install_args)
                    await log(
                        db,
                        deployment,
                        "post_install",
                        result.stdout or result.stderr,
                        level=LogLevel.INFO if result.ok else LogLevel.ERROR,
                    )
                    if not result.ok:
                        raise RuntimeError(f"installing {app_asset.name} failed (exit {result.status_code}): {result.stderr}")
                deployment.app_asset_access_token = None
                await db.commit()

            for script in template.post_install_scripts:
                await log(db, deployment, "post_install", f"running post-install script {script['name']}")
                result = client.run_ps(script["script_text"])
                await log(
                    db,
                    deployment,
                    "post_install",
                    result.stdout or result.stderr,
                    level=LogLevel.INFO if result.ok else LogLevel.ERROR,
                )
                if not result.ok:
                    raise RuntimeError(f"post-install script {script['name']} failed: {result.stderr}")

            await _state_machine.transition(db, deployment, DeploymentState.CONFIGURING)

            if template.domain_join_enabled and template.domain_join_timing == DomainJoinTiming.POST_INSTALL:
                await log(db, deployment, "configuring", f"joining domain {template.domain_fqdn}")
                result = client.join_domain(
                    template.domain_fqdn, template.domain_join_account, template.domain_join_credential,
                    template.domain_target_ou,
                )
                if not result.ok:
                    raise RuntimeError(f"domain join failed: {result.stderr}")

            await log(db, deployment, "configuring", "rebooting to finalize configuration")
            try:
                client.reboot()
            except Exception:  # noqa: BLE001 - the guest tearing down the WinRM connection mid-reboot is expected
                pass
            await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
            for _ in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
                if client.is_reachable():
                    break
                await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
            else:
                raise RuntimeError("guest did not come back reachable after the post-install reboot")

            await log(db, deployment, "configuring", "closing WinRM access, post-install is finished")
            try:
                # This command is itself the one thing that severs the
                # channel it's running over: removing the firewall rule and
                # disabling PSRemoting is safe to do inline (doesn't drop
                # the current session), but stopping the WinRM service
                # itself would, so that part runs detached a few seconds
                # later, after this call has already returned its result.
                client.run_ps(
                    "Get-NetFirewallRule -DisplayName 'DeployCore WinRM' -ErrorAction SilentlyContinue "
                    "| Remove-NetFirewallRule; "
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
            await notifications.maybe_email(
                db, ctx["redis"], user_id=deployment.created_by_user_id, event_type="complete",
                subject=f"Deployment {deployment.hostname} completed",
                body=f"Deployment {deployment.hostname} completed successfully.",
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
