from arq.connections import RedisSettings
from arq.cron import cron

from app.config import get_settings
from worker.tasks.backup import run_scheduled_backup
from worker.tasks.hypervisor import test_hypervisor_connection
from worker.tasks.maintenance import check_deployment_health, sweep_stale_deployments
from worker.tasks.notifications import send_email_notification
from worker.tasks.provision import cleanup_deployment, run_deployment, run_post_install, wait_for_callback
from worker.tasks.webhooks import deliver_webhook


class WorkerSettings:
    functions: list = [
        test_hypervisor_connection,
        run_deployment,
        wait_for_callback,
        run_post_install,
        cleanup_deployment,
        run_scheduled_backup,
        send_email_notification,
        deliver_webhook,
    ]
    cron_jobs = [
        cron(sweep_stale_deployments, minute=set(range(0, 60, 5))),
        cron(check_deployment_health, minute=set(range(0, 60, 15))),
        cron(run_scheduled_backup, hour={3}, minute={0}),
    ]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
