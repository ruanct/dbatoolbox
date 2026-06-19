from celery import shared_task


@shared_task
def probe_all_database_instances() -> dict:
    from .probe_services import probe_all_instances

    return probe_all_instances()
