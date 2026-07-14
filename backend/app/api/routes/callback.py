from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models.deployment import Deployment, DeploymentState

router = APIRouter(prefix="/api/callback", tags=["callback"])


@router.post("/{deployment_token}", status_code=status.HTTP_204_NO_CONTENT)
async def deployment_callback(deployment_token: str, request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """Authenticated by the single-use per-deployment token itself, not by
    a user session, the caller is the guest VM's FirstLogonCommands step,
    not an operator. Doesn't transition state itself: run_deployment
    already moves the deployment into installing_os right after it's done
    everything it can to get Setup running (see worker/tasks/provision.py),
    since there's no way to observe real progress during Setup itself, this
    callback firing is just the signal that install.wim's copy has fully
    landed and the guest is up, worker/tasks/provision.py's
    wait_for_callback polls callback_token_used (set here) for exactly
    that, and the installing_os -> post_install transition happens once
    it's actually confirmed reachable over WinRM."""
    result = await db.execute(select(Deployment).where(Deployment.callback_token == deployment_token))
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown callback token")
    if deployment.callback_token_used:
        raise HTTPException(status.HTTP_409_CONFLICT, "callback token already used")
    if deployment.state != DeploymentState.INSTALLING_OS:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"deployment is not awaiting a callback (state={deployment.state.value})"
        )

    deployment.callback_token_used = True
    # The caller of this endpoint IS the guest, by definition - no need
    # to separately ask the hypervisor what its address is afterward.
    # post_install prefers this over HypervisorDriver.get_guest_ip(),
    # which depends entirely on VMware Tools being installed in the guest
    # to report anything at all; a real deployment got stuck on exactly
    # that gap even after Setup and this very callback had both already
    # succeeded. APP_PUBLIC_URL is documented as the plain-HTTP origin
    # (not the HTTPS reverse proxy), so this connection reaches the API
    # directly - request.client.host is the guest's real address, not a
    # proxy's.
    if request.client is not None:
        deployment.guest_reported_ip = request.client.host
    await db.commit()


@router.post("/{deployment_token}/network-ping", status_code=status.HTTP_204_NO_CONTENT)
async def deployment_network_ping(deployment_token: str, request: Request, db: AsyncSession = Depends(get_db)) -> None:
    """Called from the specialize pass (_specialize_network_ping.xml.j2), well
    before Setup is actually done - reports "the guest has network
    connectivity and this is its current address" only, nothing more.

    Deliberately does NOT set callback_token_used: an earlier version had
    the specialize-pass command hit the main callback route above
    directly, which broke the one invariant wait_for_callback's whole
    poll loop depends on - that callback_token_used being true means
    Setup is actually, fully done. It used to only ever get set from
    FirstLogonCommands, which only runs after Setup completely finishes
    (OOBE included, past any of Setup's own internal reboots). Specialize
    runs minutes earlier than that, well before OOBE - reusing the same
    endpoint meant wait_for_callback could see callback_token_used flip
    true while Setup was still actively mid-install, immediately ejecting
    the install ISO (which Setup might still need) and hand off to
    run_post_install polling WinRM against a guest that wasn't actually
    up yet, confirmed on a real deployment that looked stuck with nothing
    network-related to point at.

    guest_reported_ip set here is still useful well before that, though:
    wait_for_callback's own WinRM-reachability fallback
    (_guest_reachable_over_winrm) can use it as soon as it's available
    instead of needing VMware Tools (not installed until after
    installing_os finishes) to report a DHCP guest's address - that
    fallback only ever proceeds once WinRM is genuinely, repeatedly
    reachable, which is real evidence of completion the same way the
    real callback is, so nothing here shortcuts that guarantee. Safe to
    call repeatedly/early: idempotent, just keeps the address current."""
    result = await db.execute(select(Deployment).where(Deployment.callback_token == deployment_token))
    deployment = result.scalar_one_or_none()
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown callback token")
    if deployment.state == DeploymentState.INSTALLING_OS and request.client is not None:
        deployment.guest_reported_ip = request.client.host
        await db.commit()
