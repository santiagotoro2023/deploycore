import pytest

from app.models import DeploymentState, Role
from app.services.deployment_service import DeploymentStateMachine, InvalidTransition, retry_deployment
from tests.conftest import (
    make_deployment,
    make_disk_layout,
    make_hypervisor_host,
    make_iso_asset,
    make_organization,
    make_template,
    make_user,
)


async def _new_deployment(db_session):
    org = await make_organization(db_session)
    user = await make_user(db_session, global_role=Role.ADMIN)
    disk_layout = await make_disk_layout(db_session, org)
    iso_asset = await make_iso_asset(db_session, org)
    template = await make_template(db_session, org, disk_layout, iso_asset)
    host = await make_hypervisor_host(db_session, org)
    return await make_deployment(db_session, org, template, host, user)


async def test_full_happy_path_transitions_succeed(db_session):
    deployment = await _new_deployment(db_session)
    machine = DeploymentStateMachine()
    happy_path = [
        DeploymentState.CREATING_VM,
        DeploymentState.BOOTING,
        DeploymentState.INSTALLING_OS,
        DeploymentState.POST_INSTALL,
        DeploymentState.CONFIGURING,
        DeploymentState.COMPLETED,
    ]
    for to_state in happy_path:
        await machine.transition(db_session, deployment, to_state)
    assert deployment.state == DeploymentState.COMPLETED


async def test_disallowed_transition_raises(db_session):
    deployment = await _new_deployment(db_session)
    machine = DeploymentStateMachine()
    with pytest.raises(InvalidTransition):
        await machine.transition(db_session, deployment, DeploymentState.INSTALLING_OS)


@pytest.mark.parametrize(
    "from_state",
    [
        DeploymentState.PENDING,
        DeploymentState.CREATING_VM,
        DeploymentState.BOOTING,
        DeploymentState.INSTALLING_OS,
        DeploymentState.POST_INSTALL,
        DeploymentState.CONFIGURING,
    ],
)
async def test_failed_reachable_from_every_non_terminal_state(db_session, from_state):
    deployment = await _new_deployment(db_session)
    deployment.state = from_state
    await db_session.commit()
    machine = DeploymentStateMachine()
    await machine.transition(db_session, deployment, DeploymentState.FAILED, detail="boom")
    assert deployment.state == DeploymentState.FAILED
    assert deployment.error_message == "boom"


async def test_terminal_state_rejects_further_transitions(db_session):
    deployment = await _new_deployment(db_session)
    deployment.state = DeploymentState.COMPLETED
    await db_session.commit()
    machine = DeploymentStateMachine()
    with pytest.raises(InvalidTransition):
        await machine.transition(db_session, deployment, DeploymentState.FAILED)


async def test_retry_resets_failed_deployment_to_pending(db_session):
    deployment = await _new_deployment(db_session)
    deployment.state = DeploymentState.FAILED
    deployment.error_message = "something broke"
    deployment.vm_moref = "vm-123"
    await db_session.commit()

    await retry_deployment(db_session, deployment)

    assert deployment.state == DeploymentState.PENDING
    assert deployment.error_message is None
    assert deployment.vm_moref is None
    assert deployment.retry_count == 1


async def test_retry_rejects_non_failed_deployment(db_session):
    deployment = await _new_deployment(db_session)
    with pytest.raises(InvalidTransition):
        await retry_deployment(db_session, deployment)
