from arq import func
from arq.connections import RedisSettings
from arq.cron import cron

from app.config import get_settings
from worker.tasks.backup import run_scheduled_backup
from worker.tasks.hypervisor import test_hypervisor_connection
from worker.tasks.maintenance import check_deployment_health, sweep_stale_deployments
from worker.tasks.notifications import send_email_notification, send_teams_notification
from worker.tasks.provision import cleanup_deployment, run_deployment, run_post_install, wait_for_callback
from worker.tasks.webhooks import deliver_webhook

# arq's own default job_timeout is 300 seconds - fine for everything else
# in this file, but silently fatal for the three provisioning jobs that
# are designed to run far longer than that. Discovered live: a real
# deployment's guest booted, logged in, enabled WinRM, and successfully
# called back - confirmed independently via the API's own "callback token
# already used" response - yet the deployment sat in installing_os
# forever anyway. The worker's own logs showed why: "wait_for_callback
# failed, TimeoutError" at exactly 300.00s, arq forcibly cancelling the
# job mid-poll, unrelated to anything about the callback, WinRM, or the
# network - every deployment that's ever gone "stuck" this session was
# almost certainly hitting this, well before any of the actual causes
# investigated (AutoLogon, static IP, network segmentation, the WinRM
# reachability hang) got a real chance to matter.
#
# wait_for_callback polls for up to the org's own os_install_timeout_minutes
# (default 90, operator-configurable with no enforced upper bound), so its
# arq timeout needs a generous static ceiling well beyond any realistic
# setting - not tied to that per-org value, which arq has no way to read
# per-job anyway. sweep_stale_deployments (cron, every 5 minutes,
# independent of any single job's lifetime) is the actual safety net for
# a deployment whose configured timeout exceeds even this: it force-fails
# based on the row's own updated_at, regardless of what any specific arq
# job is doing.
#
# run_post_install is never independently enqueued (grep confirms: the
# only call is `await run_post_install(ctx, deployment_id)` directly
# inside wait_for_callback, once the callback lands) - it runs inside
# wait_for_callback's own job execution and shares its timeout, not a
# separate one. Its own func() entry below only matters if something
# ever enqueues it directly in the future (e.g. a "retry post-install"
# admin action) - it's not what protects today's actual call path.
# wait_for_callback's own ceiling has to cover BOTH phases combined: up
# to os_install_timeout_minutes of polling, plus the entire post-install
# sequence after that (feature installs, app installs, post-install
# scripts, domain join, a reboot and its own WINRM_REACHABILITY_MAX_ATTEMPTS
# wait again) - 4 hours total is generous for the 90-minute default plus
# realistically anything short of an extreme app-install list.
# run_deployment gets a smaller but still real bump for a slow
# first-time multi-gigabyte ISO upload.
_LONG_RUNNING_JOB_TIMEOUT_SECONDS = {
    "run_deployment": 20 * 60,
    "wait_for_callback": 4 * 60 * 60,
    "run_post_install": 2 * 60 * 60,
}


class WorkerSettings:
    functions: list = [
        test_hypervisor_connection,
        func(run_deployment, timeout=_LONG_RUNNING_JOB_TIMEOUT_SECONDS["run_deployment"]),
        func(wait_for_callback, timeout=_LONG_RUNNING_JOB_TIMEOUT_SECONDS["wait_for_callback"]),
        func(run_post_install, timeout=_LONG_RUNNING_JOB_TIMEOUT_SECONDS["run_post_install"]),
        cleanup_deployment,
        run_scheduled_backup,
        send_email_notification,
        send_teams_notification,
        deliver_webhook,
    ]
    cron_jobs = [
        cron(sweep_stale_deployments, minute=set(range(0, 60, 5))),
        cron(check_deployment_health, minute=set(range(0, 60, 15))),
        cron(run_scheduled_backup, hour={3}, minute={0}),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
