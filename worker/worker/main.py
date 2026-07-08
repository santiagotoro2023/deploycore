from arq.connections import RedisSettings
from arq.cron import cron

from app.config import get_settings
from worker.tasks.hypervisor import test_hypervisor_connection
from worker.tasks.maintenance import sweep_stale_deployments
from worker.tasks.provision import cleanup_deployment, run_deployment, run_post_install, wait_for_callback


class WorkerSettings:
    functions: list = [
        test_hypervisor_connection,
        run_deployment,
        wait_for_callback,
        run_post_install,
        cleanup_deployment,
    ]
    cron_jobs = [cron(sweep_stale_deployments, minute=set(range(0, 60, 5)))]
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
