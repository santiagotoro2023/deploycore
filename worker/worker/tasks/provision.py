import asyncio
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from app.db import SessionLocal
from app.hypervisors import get_driver
from app.hypervisors.base import HypervisorDriver, VmSpec
from app.hypervisors.defaults import HYPERVISOR_DEFAULTS
from app.models.deployment import Deployment, DeploymentState, IpMode, LogLevel
from app.models.disk_layout import DiskLayout
from app.models.hypervisor import HypervisorHost
from app.models.iso_asset import IsoAsset, IsoKind
from app.models.template import DeploymentTemplate, DomainJoinTiming
from app.services import notifications, settings_resolver, webhooks
from app.services.deployment_service import DeploymentStateMachine, InvalidTransition, log
from app.services.iso_builder import build_and_upload_answer_iso
from app.services.template_render import render_autounattend
from app.winrm.client import WinRMClient, netmask_to_prefix

_state_machine = DeploymentStateMachine()

WINDOWS_ISO_UNIT = 0
ANSWER_ISO_UNIT = 1
VIRTIO_ISO_UNIT = 2

CALLBACK_POLL_INTERVAL_SECONDS = 15
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


async def _cleanup_answer_iso(driver: HypervisorDriver, deployment: Deployment) -> None:
    if deployment.answer_iso_remote_path:
        try:
            await driver.delete_iso_from_datastore(deployment.answer_iso_remote_path)
        except Exception:  # noqa: BLE001 - best-effort cleanup, already-failed deployment
            pass
        deployment.answer_iso_remote_path = None


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
    await _cleanup_answer_iso(driver, deployment)
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
        template = await db.get(DeploymentTemplate, deployment.template_id)
        disk_layout = await db.get(DiskLayout, template.disk_layout_id)
        host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
        driver = get_driver(host)
        defaults = HYPERVISOR_DEFAULTS[host.type.value]

        if template.iso_asset_id is None:
            await _fail(ctx, db, driver, deployment, "template has no Windows ISO configured")
            return
        windows_iso = await db.get(IsoAsset, template.iso_asset_id)

        current_step = "starting the provisioning pipeline"
        try:
            await _state_machine.transition(db, deployment, DeploymentState.CREATING_VM)

            current_step = "rendering autounattend.xml"
            rendered_xml = render_autounattend(deployment, template, disk_layout)

            current_step = "building and uploading the answer-file ISO"
            await log(db, deployment, "creating_vm", f"building answer-file ISO for {deployment.hostname}")
            answer_iso_remote_path = await build_and_upload_answer_iso(driver, deployment, rendered_xml)
            deployment.answer_iso_remote_path = answer_iso_remote_path
            await db.commit()

            current_step = "uploading the Windows ISO to the datastore"
            await log(db, deployment, "creating_vm", f"uploading {windows_iso.filename} to the datastore")
            windows_iso_remote_path = await driver.upload_iso_to_datastore(
                windows_iso.storage_path, windows_iso.filename
            )

            current_step = "creating the VM"
            spec = VmSpec(
                name=deployment.hostname,
                cpu_count=template.cpu_count,
                ram_mb=template.ram_mb,
                disk_size_gb=template.disk_size_gb,
                firmware=defaults["firmware"],
                scsi_controller=defaults["scsi_controller"],
                network_name=template.network_name,
                datastore=host.default_datastore,
            )
            vm_ref = await driver.create_vm(spec)
            deployment.vm_moref = vm_ref
            await db.commit()
            await log(db, deployment, "creating_vm", f"VM created ({vm_ref})")

            current_step = "attaching the Windows ISO"
            await driver.attach_iso(vm_ref, windows_iso_remote_path, WINDOWS_ISO_UNIT)

            current_step = "attaching the answer-file ISO"
            await driver.attach_iso(vm_ref, answer_iso_remote_path, ANSWER_ISO_UNIT)

            if defaults["requires_driver_injection"]:
                current_step = "attaching the VirtIO driver ISO"
                virtio_iso = await _get_virtio_iso(db, host.org_id)
                if virtio_iso is not None:
                    virtio_remote_path = await driver.upload_iso_to_datastore(
                        virtio_iso.storage_path, virtio_iso.filename
                    )
                    await driver.attach_iso(vm_ref, virtio_remote_path, VIRTIO_ISO_UNIT)

            current_step = "setting the boot order"
            await driver.set_boot_order(vm_ref, ["cdrom", "disk"])
            await log(db, deployment, "creating_vm", "VM created, media attached, powering on")

            current_step = "powering on the VM"
            await _state_machine.transition(db, deployment, DeploymentState.BOOTING)
            await driver.power_on(vm_ref)
            await log(db, deployment, "booting", "VM powered on, awaiting guest OS install callback")
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator via the log/error_message
            await _fail(ctx, db, driver, deployment, f"failed while {current_step}: {exc}", traceback.format_exc())
            return

    await ctx["redis"].enqueue_job("wait_for_callback", deployment_id)


async def wait_for_callback(ctx, deployment_id: str) -> None:
    async with SessionLocal() as db:
        deployment = await db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None or deployment.state in (DeploymentState.COMPLETED, DeploymentState.FAILED):
            return

        # If the guest's callback already landed (and flipped state past
        # BOOTING) before this job even started, skip straight to
        # post-install instead of polling, the race is real since the
        # callback route and this job both react to the same VM boot.
        if deployment.state == DeploymentState.BOOTING:
            host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
            driver = get_driver(host)
            timeout_minutes = await settings_resolver.resolve(
                db, "os_install_timeout_minutes", org_id=deployment.org_id, template_id=deployment.template_id
            )
            deadline = datetime.now(timezone.utc) + timedelta(minutes=timeout_minutes)

            while datetime.now(timezone.utc) < deadline:
                await db.refresh(deployment)
                if deployment.state != DeploymentState.BOOTING:
                    break  # callback route already advanced this deployment
                await asyncio.sleep(CALLBACK_POLL_INTERVAL_SECONDS)
            else:
                await _fail(ctx, db, driver, deployment, "timed out waiting for the guest OS install callback")
                return

    await run_post_install(ctx, deployment_id)


async def run_post_install(ctx, deployment_id: str) -> None:
    async with SessionLocal() as db:
        deployment = await db.get(Deployment, uuid.UUID(deployment_id))
        if deployment is None:
            return
        template = await db.get(DeploymentTemplate, deployment.template_id)
        host = await db.get(HypervisorHost, deployment.hypervisor_host_id)
        driver = get_driver(host)

        try:
            await log(db, deployment, "post_install", "waiting for guest IP address")
            guest_ip = None
            for _ in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
                guest_ip = await driver.get_guest_ip(deployment.vm_moref)
                if guest_ip:
                    break
                await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
            if not guest_ip:
                raise RuntimeError("guest never reported an IP address")

            client = WinRMClient(guest_ip, "Administrator", template.local_admin_password)
            for _ in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
                if client.is_reachable():
                    break
                await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
            else:
                raise RuntimeError(f"guest at {guest_ip} never became reachable over WinRM")

            await _state_machine.transition(db, deployment, DeploymentState.POST_INSTALL)

            if deployment.ip_mode == IpMode.STATIC:
                await log(db, deployment, "post_install", f"applying static network config {deployment.static_ip}")
                result = client.set_static_network(
                    deployment.static_ip,
                    netmask_to_prefix(deployment.static_netmask),
                    deployment.static_gateway,
                    deployment.static_dns or [],
                )
                if not result.ok:
                    raise RuntimeError(f"static network config failed: {result.stderr}")
                # the guest is no longer reachable at its DHCP address once
                # this takes effect, reconnect on the new static address
                # for every subsequent call in this phase.
                client = WinRMClient(deployment.static_ip, "Administrator", template.local_admin_password)
                for _ in range(WINRM_REACHABILITY_MAX_ATTEMPTS):
                    if client.is_reachable():
                        break
                    await asyncio.sleep(WINRM_REACHABILITY_POLL_INTERVAL_SECONDS)
                else:
                    raise RuntimeError(f"guest at {deployment.static_ip} never became reachable after static network config")

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

            await _cleanup_answer_iso(driver, deployment)
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
