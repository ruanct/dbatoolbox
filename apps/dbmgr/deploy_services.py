"""数据库部署业务逻辑。"""
from __future__ import annotations

import re
from typing import Any

from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone

from .deploy_constants import (
    DEPLOY_STEPS,
    JOB_TYPE_CHOICES,
    JOB_TYPE_ENGINE_MAP,
    MYSQL_DBA_ACCOUNT_TYPE,
    MYSQL_ROOT_ACCOUNT_TYPE,
    MYSQL_ROOT_GRANT_HOST,
)
from .models import DatabaseAccount, DatabaseInstance, DatabaseInstanceHost, DbDeployJob, DbDeployJobStep
from .profile_loader import list_profiles, resolve_deploy_params
from .services import ServiceError

JOB_STATUS_LABELS = dict(DbDeployJob.STATUS_CHOICES)
JOB_TYPE_LABELS = dict(JOB_TYPE_CHOICES)
STEP_STATUS_LABELS = dict(DbDeployJobStep.STEP_STATUS_CHOICES)

_SENSITIVE_KEYS = re.compile(
    r"(password|pswd|secret|token)",
    re.IGNORECASE,
)


def mask_sensitive_data(data: Any) -> Any:
    if isinstance(data, dict):
        masked: dict[str, Any] = {}
        for key, value in data.items():
            if _SENSITIVE_KEYS.search(str(key)):
                masked[key] = "******"
            else:
                masked[key] = mask_sensitive_data(value)
        return masked
    if isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    return data


def _serialize_step(step: DbDeployJobStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "step_code": step.step_code,
        "step_name": step.step_name,
        "status": step.status,
        "status__display": STEP_STATUS_LABELS.get(step.status, step.status),
        "output": step.output,
        "sort_order": step.sort_order,
        "started_at": step.started_at.strftime("%Y-%m-%d %H:%M:%S") if step.started_at else "",
        "finished_at": step.finished_at.strftime("%Y-%m-%d %H:%M:%S") if step.finished_at else "",
    }


def serialize_deploy_job(obj: DbDeployJob, *, include_steps: bool = False) -> dict[str, Any]:
    profile_code = (obj.params or {}).get("meta", {}).get("version_profile_code", "")
    if not profile_code:
        profile_code = (obj.resolved_params or {}).get("meta", {}).get("version_profile_code", "")
    data = {
        "id": obj.id,
        "job_type": obj.job_type,
        "job_type__display": JOB_TYPE_LABELS.get(obj.job_type, obj.job_type),
        "status": obj.status,
        "status__display": JOB_STATUS_LABELS.get(obj.status, obj.status),
        "target_host_id": obj.target_host_id,
        "target_host__display": str(obj.target_host),
        "environment_id": obj.environment_id,
        "environment__display": obj.environment.name,
        "business_id": obj.business_id,
        "business__display": obj.business.name,
        "version_profile_code": profile_code,
        "instance_id": obj.instance_id,
        "instance__display": obj.instance.instance_name if obj.instance_id else "",
        "creator": obj.creator,
        "remark": obj.remark,
        "error_message": obj.error_message,
        "params": mask_sensitive_data(obj.params or {}),
        "resolved_params": mask_sensitive_data(obj.resolved_params or {}),
        "result": obj.result or {},
        "started_at": obj.started_at.strftime("%Y-%m-%d %H:%M:%S") if obj.started_at else "",
        "finished_at": obj.finished_at.strftime("%Y-%m-%d %H:%M:%S") if obj.finished_at else "",
        "created_at": obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if include_steps:
        data["steps"] = [_serialize_step(step) for step in obj.steps.all()]
    return data


def get_deploy_form_options() -> dict[str, Any]:
    from apps.common.models import Business, Environment, Host, HostIP

    hosts = []
    for host in Host.objects.select_related("os_type").order_by("display_name"):
        ip = (
            HostIP.objects.filter(host_id=host.id, ip_type="business")
            .order_by("id")
            .values_list("ip_address", flat=True)
            .first()
        )
        hosts.append({
            "id": host.id,
            "display_name": host.display_name,
            "hostname": host.hostname,
            "os_type": host.os_type.name if host.os_type_id else "",
            "business_ip": ip or "",
        })
    return {
        "job_types": [{"value": v, "label": l} for v, l in JOB_TYPE_CHOICES],
        "hosts": hosts,
        "environments": list(Environment.objects.values("id", "name", "code")),
        "businesses": list(Business.objects.values("id", "name")),
        "profiles": list_profiles(),
    }


def list_deploy_profiles(*, job_type: str | None = None, engine: str | None = None) -> dict[str, Any]:
    return {"code": 0, "msg": "", "data": list_profiles(job_type=job_type, engine=engine)}


def _validate_create_body(body: dict[str, Any]) -> dict[str, Any]:
    job_type = (body.get("job_type") or "").strip()
    if job_type not in JOB_TYPE_LABELS:
        raise ServiceError("请选择部署类型")

    host_id = body.get("target_host_id") or body.get("host_id")
    if not host_id:
        raise ServiceError("请选择目标主机")

    environment_id = body.get("environment_id")
    business_id = body.get("business_id")
    if not environment_id:
        raise ServiceError("请选择所属环境")
    if not business_id:
        raise ServiceError("请选择所属业务")

    profile_code = (body.get("version_profile_code") or "").strip()
    if not profile_code:
        raise ServiceError("请选择版本档案")

    user_params = body.get("params") if isinstance(body.get("params"), dict) else body
    meta = user_params.get("meta") if isinstance(user_params.get("meta"), dict) else {}
    meta.setdefault("job_type", job_type)
    meta["version_profile_code"] = profile_code
    user_params["meta"] = meta

    cmdb = user_params.setdefault("cmdb", {})
    cmdb["instance_name"] = (cmdb.get("instance_name") or body.get("instance_name") or "").strip()
    if not cmdb["instance_name"]:
        raise ServiceError("请填写实例名称")
    if DatabaseInstance.objects.filter(instance_name=cmdb["instance_name"]).exists():
        raise ServiceError("实例名称已存在")

    engine = JOB_TYPE_ENGINE_MAP[job_type]
    if engine == "oracle":
        sid = (cmdb.get("sid") or "").strip()
        service_name = (cmdb.get("service_name") or "").strip()
        if not sid and not service_name:
            raise ServiceError("Oracle 需填写 SID 或 Service Name")

    credentials = user_params.setdefault("credentials", {})
    if engine == "mysql":
        if not credentials.get("root_password"):
            raise ServiceError("请填写 MySQL root 密码")
        port = cmdb.get("port")
        if not port:
            raise ServiceError("请填写 MySQL 端口")
        port_num = int(port)
        if port_num < 1024 or port_num > 65535:
            raise ServiceError("MySQL 端口无效")
        if not (cmdb.get("connect_host") or "").strip():
            raise ServiceError("请填写 MySQL 连接地址")
        config = user_params.setdefault("config", {})
        enable_binlog = config.get("enable_binlog", True)
        enable_gtid = config.get("enable_gtid", True)
        if isinstance(enable_binlog, str):
            enable_binlog = enable_binlog.strip().lower() in {"1", "true", "yes", "on"}
        if isinstance(enable_gtid, str):
            enable_gtid = enable_gtid.strip().lower() in {"1", "true", "yes", "on"}
        if enable_gtid:
            enable_binlog = True
        if enable_gtid and not enable_binlog:
            raise ServiceError("开启 GTID 必须先开启 Binlog")
    elif engine == "oracle":
        if not credentials.get("sys_password"):
            raise ServiceError("请填写 Oracle SYS 密码")

    return {
        "job_type": job_type,
        "host_id": int(host_id),
        "environment_id": int(environment_id),
        "business_id": int(business_id),
        "profile_code": profile_code,
        "user_params": user_params,
        "creator": (body.get("creator") or "").strip(),
        "remark": (body.get("remark") or "").strip(),
    }


def create_deploy_job(body: dict[str, Any]) -> dict[str, Any]:
    fields = _validate_create_body(body)
    resolved = resolve_deploy_params(
        job_type=fields["job_type"],
        profile_code=fields["profile_code"],
        user_params=fields["user_params"],
        host_id=fields["host_id"],
    )

    with transaction.atomic():
        job = DbDeployJob.objects.create(
            job_type=fields["job_type"],
            status="pending",
            target_host_id=fields["host_id"],
            environment_id=fields["environment_id"],
            business_id=fields["business_id"],
            params=fields["user_params"],
            resolved_params=resolved,
            creator=fields["creator"],
            remark=fields["remark"],
        )
        for index, (step_code, step_name) in enumerate(DEPLOY_STEPS):
            DbDeployJobStep.objects.create(
                job=job,
                step_code=step_code,
                step_name=step_name,
                sort_order=index + 1,
            )

    from .deploy_tasks import run_db_deploy_job

    run_db_deploy_job.delay(job.id)
    return {"code": 0, "msg": "部署任务已创建", "data": {"job_id": job.id}}


def _get_deploy_job_or_raise(job_id: int) -> DbDeployJob:
    try:
        return DbDeployJob.objects.get(id=job_id)
    except DbDeployJob.DoesNotExist as exc:
        raise ServiceError("部署任务不存在", 404) from exc


def _enqueue_deploy_job(job_id: int) -> None:
    from .deploy_tasks import run_db_deploy_job

    run_db_deploy_job.delay(job_id)


def retry_deploy_job(job_id: int) -> dict[str, Any]:
    """重新投递 Celery 任务；失败/已取消任务会重置步骤后重试。"""
    job = _get_deploy_job_or_raise(job_id)
    if job.status in {"running", "prechecking", "verifying"}:
        raise ServiceError("任务执行中，无法重试")
    if job.status == "succeeded":
        raise ServiceError("任务已成功，无需重试")

    with transaction.atomic():
        if job.status in {"failed", "cancelled"}:
            job.steps.update(
                status="pending",
                output="",
                started_at=None,
                finished_at=None,
            )
        profile_code = (
            (job.params or {}).get("meta", {}).get("version_profile_code")
            or (job.resolved_params or {}).get("meta", {}).get("version_profile_code")
            or ""
        )
        if profile_code:
            job.resolved_params = resolve_deploy_params(
                job_type=job.job_type,
                profile_code=profile_code,
                user_params=job.params or {},
                host_id=job.target_host_id,
            )
        job.status = "pending"
        job.error_message = ""
        job.started_at = None
        job.finished_at = None
        job.save(update_fields=[
            "status", "error_message", "started_at", "finished_at",
            "resolved_params", "updated_at",
        ])

    _enqueue_deploy_job(job.id)
    return {"code": 0, "msg": "已重新提交执行", "data": {"job_id": job.id}}


def cancel_deploy_job(job_id: int) -> dict[str, Any]:
    """取消待执行任务（无法撤回已在 Worker 中运行的 Ansible 步骤）。"""
    job = _get_deploy_job_or_raise(job_id)
    if job.status != "pending":
        raise ServiceError("仅待执行状态的任务可取消")

    job.status = "cancelled"
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at", "updated_at"])
    return {"code": 0, "msg": "任务已取消", "data": {"job_id": job.id}}


def delete_deploy_job(job_id: int) -> dict[str, Any]:
    job = _get_deploy_job_or_raise(job_id)
    if job.status in {"running", "prechecking", "verifying"}:
        raise ServiceError("执行中的任务不可删除")
    if job.status == "succeeded":
        raise ServiceError("已成功的任务不可删除")

    job.delete()
    return {"code": 0, "msg": "任务已删除", "data": {"job_id": job_id}}


def list_deploy_jobs(*, page: int = 1, limit: int = 20, keyword: str = "") -> dict[str, Any]:
    from django.db.models import Q

    queryset = (
        DbDeployJob.objects.select_related(
            "target_host", "environment", "business", "instance",
        )
        .order_by("-id")
    )
    if keyword:
        queryset = queryset.filter(
            Q(params__cmdb__instance_name__icontains=keyword)
            | Q(target_host__display_name__icontains=keyword)
            | Q(creator__icontains=keyword),
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_deploy_job(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def get_deploy_job_detail(job_id: int) -> dict[str, Any]:
    try:
        job = (
            DbDeployJob.objects.select_related(
                "target_host", "environment", "business", "instance",
            )
            .prefetch_related("steps")
            .get(id=job_id)
        )
    except DbDeployJob.DoesNotExist as exc:
        raise ServiceError("部署任务不存在", 404) from exc
    return {"code": 0, "msg": "", "data": serialize_deploy_job(job, include_steps=True)}


def register_instance_from_job(job: DbDeployJob) -> DatabaseInstance:
    resolved = job.resolved_params or {}
    cmdb = resolved.get("cmdb") or {}
    credentials = resolved.get("credentials") or {}
    engine = resolved.get("meta", {}).get("engine") or JOB_TYPE_ENGINE_MAP.get(job.job_type, "")

    instance = DatabaseInstance.objects.create(
        instance_name=cmdb["instance_name"],
        engine=engine,
        topology=cmdb.get("topology", "standalone"),
        role=cmdb.get("role", "master"),
        status="online",
        version=(job.result or {}).get("detected_version", ""),
        environment_id=job.environment_id,
        business_id=job.business_id,
        connect_host=cmdb.get("connect_host", ""),
        port=int(cmdb.get("port") or 3306),
        db_name=(cmdb.get("db_name") or "").strip(),
        charset=(cmdb.get("charset") or "").strip(),
        sid=(cmdb.get("sid") or "").strip(),
        service_name=(cmdb.get("service_name") or "").strip(),
        remark=(cmdb.get("remark") or job.remark or "").strip(),
    )
    DatabaseInstanceHost.objects.create(
        instance=instance,
        host_id=job.target_host_id,
        node_name=resolved.get("target", {}).get("hostname", ""),
        listener_port=int(cmdb.get("port") or 3306),
        is_primary=True,
        sort_order=1,
    )
    admin = credentials.get("admin_account") or {}
    if engine == "mysql":
        root_password = (credentials.get("root_password") or "").strip()
        if root_password:
            DatabaseAccount.objects.create(
                instance=instance,
                account_type=MYSQL_ROOT_ACCOUNT_TYPE,
                account_name="root",
                grant_host=MYSQL_ROOT_GRANT_HOST,
                account_pswd=root_password,
                is_default=False,
            )
    if admin.get("account_name") and admin.get("account_pswd"):
        DatabaseAccount.objects.create(
            instance=instance,
            account_type=MYSQL_DBA_ACCOUNT_TYPE,
            account_name=admin["account_name"],
            grant_host=(admin.get("grant_host") or "%").strip() if engine == "mysql" else "",
            account_pswd=admin["account_pswd"],
            is_default=True,
        )
    job.instance = instance
    job.save(update_fields=["instance"])
    return instance


def mark_job_running(job: DbDeployJob, status: str) -> None:
    job.status = status
    if not job.started_at:
        job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at", "updated_at"])


def mark_job_finished(job: DbDeployJob, *, success: bool, error_message: str = "") -> None:
    job.status = "succeeded" if success else "failed"
    job.error_message = error_message[:512]
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error_message", "finished_at", "updated_at"])


def update_step_status(
    step: DbDeployJobStep,
    *,
    status: str,
    output: str = "",
) -> None:
    now = timezone.now()
    if status == "running" and not step.started_at:
        step.started_at = now
    if status in {"succeeded", "failed", "skipped"}:
        step.finished_at = now
    step.status = status
    step.output = output
    step.save(update_fields=["status", "output", "started_at", "finished_at"])
