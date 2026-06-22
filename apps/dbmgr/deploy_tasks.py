from celery import shared_task


@shared_task(bind=True, max_retries=0)
def run_db_deploy_job(self, job_id: int) -> dict:
    from apps.dbmgr.deploy_executors.registry import get_executor
    from apps.dbmgr.models import DbDeployJob

    try:
        job = DbDeployJob.objects.get(id=job_id)
    except DbDeployJob.DoesNotExist:
        return {"code": 1, "msg": "任务不存在"}

    if job.status not in {"pending", "failed"}:
        return {"code": 1, "msg": f"任务状态不可执行: {job.status}"}

    executor = get_executor(job.job_type)
    executor.run(job_id)
    job.refresh_from_db()
    return {"code": 0, "msg": "ok", "status": job.status}
