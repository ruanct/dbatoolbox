from celery import shared_task

from .deploy_tasks import run_db_deploy_job  # noqa: F401 — 供 Celery autodiscover 注册


@shared_task
def probe_all_database_instances() -> dict:
    from .probe_services import probe_all_instances

    return probe_all_instances()
