"""数据库部署业务逻辑。"""
from __future__ import annotations

import json
import re
from typing import Any

from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.utils import timezone

from .deploy_constants import (
    DEPLOY_STEPS,
    JOB_TYPE_CHOICES,
    JOB_TYPE_ENGINE_MAP,
    MYSQL_DBA_ACCOUNT_TYPE,
    MYSQL_REPLICA_DEPLOY_STEPS,
    MYSQL_ROOT_ACCOUNT_TYPE,
    MYSQL_ROOT_GRANT_HOST,
    STANDALONE_DEPLOY_JOB_TYPES,
    _coerce_bool,
    apply_mysql_master_runtime,
    build_mysql_cnf_sections,
    build_mysql_server_id,
    ensure_host_deploy_lock_available,
    ensure_mysql_server_id_available,
    validate_mysql_deploy_connect_host,
)
from .models import (
    DatabaseAccount,
    DatabaseInstance,
    DatabaseInstanceHost,
    DatabaseReplicationCluster,
    DbDeployJob,
    DbDeployJobStep,
)
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
    if obj.job_type == "mysql_replica":
        ctx = (obj.params or {}).get("context") or {}
        cmdb = (obj.params or {}).get("cmdb") or {}
        data["replication_cluster_id"] = ctx.get("replication_cluster_id")
        data["master_instance_id"] = ctx.get("master_instance_id")
        data["master_instance__display"] = cmdb.get("master_instance_name") or ""
        data["bootstrap_method"] = ctx.get("bootstrap_method") or ""
        data["enable_semi_sync"] = bool(ctx.get("enable_semi_sync"))
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
        "job_types": [{"value": v, "label": l} for v, l in STANDALONE_DEPLOY_JOB_TYPES],
        "hosts": hosts,
        "environments": list(Environment.objects.values("id", "name", "code")),
        "businesses": list(Business.objects.values("id", "name")),
        "profiles": list_profiles(),
    }


def list_deploy_profiles(*, job_type: str | None = None, engine: str | None = None) -> dict[str, Any]:
    return {"code": 0, "msg": "", "data": list_profiles(job_type=job_type, engine=engine)}


def _validate_create_body(body: dict[str, Any]) -> dict[str, Any]:
    job_type = (body.get("job_type") or "").strip()
    if job_type == "mysql_replica":
        raise ServiceError("MySQL 从库请使用「添加MySQL从库」页面创建")
    standalone_types = {value for value, _ in STANDALONE_DEPLOY_JOB_TYPES}
    if job_type not in standalone_types:
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
    template_code = (meta.get("mysql_param_template_code") or body.get("mysql_param_template_code") or "").strip()
    template_title = (meta.get("mysql_param_template_title") or body.get("mysql_param_template_title") or "").strip()
    if template_code:
        meta["mysql_param_template_code"] = template_code
    if template_title:
        meta["mysql_param_template_title"] = template_title
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
        cmdb["connect_host"] = validate_mysql_deploy_connect_host(
            int(host_id),
            str(cmdb.get("connect_host") or ""),
        )
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
    from apps.common.models import Host

    fields = _validate_create_body(body)
    resolved = resolve_deploy_params(
        job_type=fields["job_type"],
        profile_code=fields["profile_code"],
        user_params=fields["user_params"],
        host_id=fields["host_id"],
    )

    with transaction.atomic():
        Host.objects.select_for_update().get(id=fields["host_id"])
        ensure_host_deploy_lock_available(fields["host_id"])
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
        job.resolved_params = resolve_deploy_params(
            job_type=fields["job_type"],
            profile_code=fields["profile_code"],
            user_params=fields["user_params"],
            host_id=fields["host_id"],
            deploy_job_id=job.id,
        )
        job.save(update_fields=["resolved_params", "updated_at"])
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


def _reset_deploy_job_steps(
    job: DbDeployJob,
    *,
    from_sort_order: int | None = None,
) -> None:
    """将部署步骤重置为待执行；from_sort_order 为 None 时重置全部步骤。"""
    steps = job.steps.all()
    if from_sort_order is not None:
        steps = steps.filter(sort_order__gte=from_sort_order)
    steps.update(
        status="pending",
        output="",
        started_at=None,
        finished_at=None,
    )


def _refresh_job_resolved_params(job: DbDeployJob) -> None:
    profile_code = (
        (job.params or {}).get("meta", {}).get("version_profile_code")
        or (job.resolved_params or {}).get("meta", {}).get("version_profile_code")
        or ""
    )
    if not profile_code:
        return
    job.resolved_params = resolve_deploy_params(
        job_type=job.job_type,
        profile_code=profile_code,
        user_params=job.params or {},
        host_id=job.target_host_id,
        deploy_job_id=job.id,
    )


def _set_force_rebuild_flag(job: DbDeployJob, *, enabled: bool) -> None:
    params = dict(job.params or {})
    meta = dict(params.get("meta") or {})
    if enabled:
        meta["force_rebuild"] = True
    else:
        meta.pop("force_rebuild", None)
    params["meta"] = meta
    job.params = params


def _clear_force_rebuild_flag(job: DbDeployJob) -> None:
    """任务结束后清除 force_rebuild，避免后续「继续执行」误触发清理。"""
    params = dict(job.params or {})
    meta = dict(params.get("meta") or {})
    changed = False
    if meta.pop("force_rebuild", None) is not None:
        params["meta"] = meta
        job.params = params
        changed = True

    resolved = dict(job.resolved_params or {})
    rmeta = dict(resolved.get("meta") or {})
    if rmeta.pop("force_rebuild", None) is not None:
        resolved["meta"] = rmeta
        job.resolved_params = resolved
        changed = True

    if changed:
        job.save(update_fields=["params", "resolved_params", "updated_at"])


def _enqueue_job_as_pending(job: DbDeployJob, *, update_resolved_params: bool) -> None:
    if update_resolved_params:
        _refresh_job_resolved_params(job)
    ensure_host_deploy_lock_available(job.target_host_id, exclude_job_id=job.id)
    job.status = "pending"
    job.error_message = ""
    job.started_at = None
    job.finished_at = None
    job.save(update_fields=[
        "status", "error_message", "started_at", "finished_at",
        "resolved_params", "params", "updated_at",
    ])


def retry_deploy_job(job_id: int) -> dict[str, Any]:
    """重新投递 Celery 任务。

    失败任务默认从首个失败步骤续跑（保留已成功步骤）；已取消任务全量重跑。
    续跑时不刷新 resolved_params，避免与已初始化实例的路径/server_id 不一致。
    """
    job = _get_deploy_job_or_raise(job_id)
    if job.status in {"running", "prechecking", "verifying"}:
        raise ServiceError("任务执行中，无法重试")
    if job.status == "succeeded":
        raise ServiceError("任务已成功，无需重试")

    resume_from_failed = False
    skipped_step_count = 0
    resume_step_name = ""

    with transaction.atomic():
        if job.status == "cancelled":
            _reset_deploy_job_steps(job)
            refresh_resolved_params = True
        elif job.status == "failed":
            failed_step = (
                job.steps.filter(status="failed")
                .order_by("sort_order", "id")
                .first()
            )
            if failed_step:
                resume_from_failed = True
                resume_step_name = failed_step.step_name
                skipped_step_count = job.steps.filter(
                    status="succeeded",
                    sort_order__lt=failed_step.sort_order,
                ).count()
                _reset_deploy_job_steps(job, from_sort_order=failed_step.sort_order)
                refresh_resolved_params = False
            else:
                _reset_deploy_job_steps(job)
                refresh_resolved_params = True
        else:
            refresh_resolved_params = True

        if refresh_resolved_params:
            _refresh_job_resolved_params(job)

        _enqueue_job_as_pending(job, update_resolved_params=False)

    _enqueue_deploy_job(job.id)
    if resume_from_failed:
        msg = f"已从失败步骤「{resume_step_name}」继续提交执行"
        if skipped_step_count:
            msg += f"（跳过 {skipped_step_count} 个已成功步骤）"
    else:
        msg = "已重新提交全量执行"
    return {
        "code": 0,
        "msg": msg,
        "data": {
            "job_id": job.id,
            "resume_from_failed": resume_from_failed,
            "skipped_step_count": skipped_step_count,
        },
    }


def release_deploy_job_endpoint(job_id: int) -> dict[str, Any]:
    """释放失败任务占用的连接端点，便于同端口重新创建部署任务。

    将任务状态置为 cancelled；不删除任务记录与步骤日志。
    """
    job = _get_deploy_job_or_raise(job_id)
    if job.status != "failed":
        raise ServiceError("仅失败状态的任务可释放端点占用")
    if job.instance_id:
        raise ServiceError("任务已注册实例台账，无法释放端点；请先在实例台账中处理")

    job.status = "cancelled"
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at", "updated_at"])
    return {
        "code": 0,
        "msg": "已释放端点占用，同连接地址与端口可重新创建部署任务",
        "data": {"job_id": job.id, "status": job.status},
    }


def force_rebuild_deploy_job(job_id: int) -> dict[str, Any]:
    """强制重建：清理目标机本任务实例目录后全量重装。

    仅支持未注册台账的 MySQL 单实例任务；执行结束后自动清除 force_rebuild 标记。
    """
    job = _get_deploy_job_or_raise(job_id)
    if job.status in {"running", "prechecking", "verifying"}:
        raise ServiceError("任务执行中，无法强制重建")
    if job.status == "succeeded":
        raise ServiceError("任务已成功，无需强制重建")
    if job.job_type != "mysql_standalone":
        raise ServiceError("当前部署类型不支持强制重建")
    if job.instance_id:
        raise ServiceError("任务已注册实例台账，无法强制重建；请先在台账中处理该实例")

    instance_root = ""
    install = (job.resolved_params or {}).get("install") or {}
    instance_root = str(install.get("instance_root") or "").strip()

    with transaction.atomic():
        _reset_deploy_job_steps(job)
        _set_force_rebuild_flag(job, enabled=True)
        _enqueue_job_as_pending(job, update_resolved_params=True)

    _enqueue_deploy_job(job.id)
    msg = "已提交强制重建（将清理实例目录后全量重装）"
    if instance_root:
        msg += f"：{instance_root}"
    return {
        "code": 0,
        "msg": msg,
        "data": {"job_id": job.id, "force_rebuild": True, "instance_root": instance_root},
    }


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
        DbDeployJob.objects.exclude(job_type="mysql_replica")
        .select_related(
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
    config = resolved.get("config") or {}
    server_id_raw = config.get("server_id")
    server_id = int(server_id_raw) if server_id_raw is not None and engine == "mysql" else None

    try:
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
            server_id=server_id,
            db_name=(cmdb.get("db_name") or "").strip(),
            charset=(cmdb.get("charset") or "").strip(),
            sid=(cmdb.get("sid") or "").strip(),
            service_name=(cmdb.get("service_name") or "").strip(),
            remark=(cmdb.get("remark") or job.remark or "").strip(),
        )
    except IntegrityError as exc:
        connect_host = (cmdb.get("connect_host") or "").strip()
        port = int(cmdb.get("port") or 3306)
        raise ServiceError(
            f"连接端点 {connect_host}:{port} 已存在，无法注册台账，请检查 CMDB 是否已有相同实例"
        ) from exc
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


def parse_deploy_output_marker(output: str, marker: str) -> dict[str, Any]:
    prefix = f"{marker}="
    for line in (output or "").splitlines():
        if prefix not in line:
            continue
        payload = line.split(prefix, 1)[1].strip()
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ServiceError(f"{marker} 格式无效")
        return data
    raise ServiceError(f"未找到 {marker} 输出")


def _repair_mysql_replica_slave_server_id_in_resolved(
    resolved: dict[str, Any],
    *,
    exclude_job_id: int | None = None,
) -> None:
    """修正从库 config.server_id：不得与主库 master_runtime.server_id 相同。"""
    config = dict(resolved.get("config") or {})
    context = resolved.get("context") or {}
    master_sid = (context.get("master_runtime") or {}).get("server_id")
    slave_sid = config.get("server_id")
    cmdb = resolved.get("cmdb") or {}
    connect_host = (cmdb.get("connect_host") or "").strip()
    port = int(cmdb.get("port") or 3306)

    need_repair = not slave_sid
    if master_sid is not None and slave_sid is not None and str(slave_sid) == str(master_sid):
        need_repair = True
    if not need_repair:
        return

    if not connect_host:
        raise ServiceError("从库缺少 connect_host，无法重新生成 server_id")

    new_sid = build_mysql_server_id(connect_host, port)
    if master_sid is not None and str(new_sid) == str(master_sid):
        raise ServiceError(
            f"无法为从库生成与主库不同的 server_id（主库={master_sid}，从库端口={port}）"
        )
    config["server_id"] = new_sid
    ensure_mysql_server_id_available(int(new_sid), exclude_job_id=exclude_job_id)
    resolved["config"] = config
    build_mysql_cnf_sections(resolved)


def repair_mysql_replica_slave_server_id(job: DbDeployJob) -> None:
    """修正任务 resolved_params 中从库 server_id（续跑/建立复制前调用）。"""
    resolved = dict(job.resolved_params or {})
    _repair_mysql_replica_slave_server_id_in_resolved(resolved, exclude_job_id=job.id)
    job.resolved_params = resolved
    job.save(update_fields=["resolved_params", "updated_at"])


def merge_master_runtime_into_job(job: DbDeployJob, runtime: dict[str, Any]) -> None:
    resolved = dict(job.resolved_params or {})
    context = dict(resolved.get("context") or {})
    context["master_runtime"] = runtime
    resolved["context"] = context

    apply_mysql_master_runtime(resolved)
    _repair_mysql_replica_slave_server_id_in_resolved(resolved, exclude_job_id=job.id)

    config = resolved.get("config") or {}
    slave_server_id = config.get("server_id")
    master_server_id = runtime.get("server_id")
    if (
        slave_server_id is not None
        and master_server_id not in (None, "")
        and str(slave_server_id) == str(master_server_id)
    ):
        raise ServiceError(f"从库 server_id {slave_server_id} 与主库冲突")

    job.resolved_params = resolved
    job.save(update_fields=["resolved_params", "updated_at"])


def save_replication_status(job: DbDeployJob, status: dict[str, Any]) -> None:
    result = dict(job.result or {})
    result["replication_status"] = status
    job.result = result
    job.save(update_fields=["result", "updated_at"])


def register_replica_instance_from_job(job: DbDeployJob) -> DatabaseInstance:
    resolved = job.resolved_params or {}
    cmdb = resolved.get("cmdb") or {}
    context = resolved.get("context") or {}
    config = resolved.get("config") or {}
    engine = resolved.get("meta", {}).get("engine") or JOB_TYPE_ENGINE_MAP.get(job.job_type, "")
    server_id_raw = config.get("server_id")
    server_id = int(server_id_raw) if server_id_raw is not None and engine == "mysql" else None
    master_instance_id = context.get("master_instance_id")
    replication_cluster_id = context.get("replication_cluster_id") or cmdb.get("replication_cluster_id")

    try:
        instance = DatabaseInstance.objects.create(
            instance_name=cmdb["instance_name"],
            engine=engine,
            topology="replication",
            role="slave",
            status="online",
            version=(job.result or {}).get("detected_version", ""),
            environment_id=job.environment_id,
            business_id=job.business_id,
            replication_cluster_id=replication_cluster_id,
            connect_host=cmdb.get("connect_host", ""),
            port=int(cmdb.get("port") or 3306),
            server_id=server_id,
            db_name=(cmdb.get("db_name") or "").strip(),
            charset=(cmdb.get("charset") or "").strip(),
            remark=(cmdb.get("remark") or job.remark or "").strip(),
        )
    except IntegrityError as exc:
        connect_host = (cmdb.get("connect_host") or "").strip()
        port = int(cmdb.get("port") or 3306)
        raise ServiceError(
            f"连接端点 {connect_host}:{port} 已存在，无法注册台账，请检查 CMDB 是否已有相同实例"
        ) from exc

    DatabaseInstanceHost.objects.create(
        instance=instance,
        host_id=job.target_host_id,
        node_name=resolved.get("target", {}).get("hostname", ""),
        listener_port=int(cmdb.get("port") or 3306),
        is_primary=True,
        sort_order=1,
    )

    if master_instance_id:
        master_accounts = DatabaseAccount.objects.filter(instance_id=int(master_instance_id)).order_by("id")
        for account in master_accounts:
            DatabaseAccount.objects.create(
                instance=instance,
                account_type=account.account_type,
                account_name=account.account_name,
                grant_host=account.grant_host,
                account_pswd=account.account_pswd,
                default_schema=account.default_schema,
                is_default=account.is_default,
                remark=account.remark,
            )

    job.instance = instance
    job.save(update_fields=["instance"])
    return instance


def ensure_mysql_replica_job_steps(job_id: int) -> None:
    """将历史从库任务的步骤列表修正为 MYSQL_REPLICA_DEPLOY_STEPS。"""
    try:
        job = DbDeployJob.objects.prefetch_related("steps").get(id=job_id, job_type="mysql_replica")
    except DbDeployJob.DoesNotExist:
        return

    existing = list(job.steps.order_by("sort_order", "id").values_list("step_code", flat=True))
    expected = [code for code, _ in MYSQL_REPLICA_DEPLOY_STEPS]
    if existing == expected:
        return

    preserved: dict[str, DbDeployJobStep] = {}
    for step in job.steps.all():
        if step.step_code in expected and step.step_code not in preserved:
            preserved[step.step_code] = step

    with transaction.atomic():
        job.steps.all().delete()
        for index, (step_code, step_name) in enumerate(MYSQL_REPLICA_DEPLOY_STEPS):
            old = preserved.get(step_code)
            DbDeployJobStep.objects.create(
                job=job,
                step_code=step_code,
                step_name=step_name,
                sort_order=index + 1,
                status=old.status if old else "pending",
                output=old.output if old else "",
                started_at=old.started_at if old else None,
                finished_at=old.finished_at if old else None,
            )


def mark_job_running(job: DbDeployJob, status: str) -> None:
    ensure_host_deploy_lock_available(job.target_host_id, exclude_job_id=job.id)
    job.status = status
    if not job.started_at:
        job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at", "updated_at"])


def mark_job_finished(job: DbDeployJob, *, success: bool, error_message: str = "") -> None:
    job.status = "succeeded" if success else "failed"
    job.error_message = error_message[:512]
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "error_message", "finished_at", "updated_at"])
    _clear_force_rebuild_flag(job)


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


_MYSQL_MAJOR_VERSION_RE = re.compile(r"^(\d+\.\d+)")


def parse_mysql_major_version(version: str) -> str:
    match = _MYSQL_MAJOR_VERSION_RE.match((version or "").strip())
    return match.group(1) if match else ""


def _build_deploy_hosts_list() -> list[dict[str, Any]]:
    from apps.common.models import Host, HostIP

    hosts: list[dict[str, Any]] = []
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
    return hosts


def _serialize_replica_cluster_option(cluster: DatabaseReplicationCluster) -> dict[str, Any]:
    master = cluster.primary_instance
    if not master:
        return {
            "id": cluster.id,
            "name": cluster.name,
            "primary_instance_id": None,
        }

    primary_deploy = (
        DatabaseInstanceHost.objects.filter(instance_id=master.id, is_primary=True)
        .select_related("host")
        .order_by("sort_order", "id")
        .first()
    )
    if not primary_deploy:
        primary_deploy = (
            DatabaseInstanceHost.objects.filter(instance_id=master.id)
            .select_related("host")
            .order_by("sort_order", "id")
            .first()
        )

    repl_accounts = list(
        DatabaseAccount.objects.filter(instance_id=master.id, account_type="user_repl")
        .order_by("id")
        .values("id", "account_name", "grant_host")
    )
    dump_account = (
        DatabaseAccount.objects.filter(
            instance_id=master.id,
            account_type=MYSQL_DBA_ACCOUNT_TYPE,
            is_default=True,
        )
        .order_by("id")
        .first()
    )

    master_deploy_host_id = primary_deploy.host_id if primary_deploy else None
    master_deploy_host_display = ""
    if primary_deploy and primary_deploy.host_id:
        master_deploy_host_display = str(primary_deploy.host)

    return {
        "id": cluster.id,
        "name": cluster.name,
        "primary_instance_id": master.id,
        "primary_instance_name": master.instance_name,
        "master_connect_host": master.connect_host,
        "master_port": master.port,
        "master_version": master.version,
        "master_major_version": parse_mysql_major_version(master.version),
        "master_environment_id": master.environment_id,
        "master_business_id": master.business_id,
        "master_deploy_host_id": master_deploy_host_id,
        "master_deploy_host__display": master_deploy_host_display,
        "repl_accounts": repl_accounts,
        "default_dump_account_id": dump_account.id if dump_account else None,
        "has_repl_account": bool(repl_accounts),
        "has_dump_account": dump_account is not None,
    }


def get_mysql_replica_form_options() -> dict[str, Any]:
    from apps.common.models import Business, Environment

    clusters = [
        _serialize_replica_cluster_option(item)
        for item in DatabaseReplicationCluster.objects.filter(
            engine="mysql",
            replication_type="mysql_replication",
            primary_instance_id__isnull=False,
        )
        .select_related("primary_instance")
        .order_by("name")
    ]
    return {
        "hosts": _build_deploy_hosts_list(),
        "environments": list(Environment.objects.values("id", "name", "code")),
        "businesses": list(Business.objects.values("id", "name")),
        "profiles": list_profiles(engine="mysql", job_type="mysql_replica"),
        "replication_clusters": clusters,
        "bootstrap_methods": [
            {"value": "mysqldump", "label": "mysqldump（第一期）"},
        ],
    }


def resolve_mysql_master_replication_endpoint(
    *,
    master: DatabaseInstance,
    slave_target_host_id: int,
) -> dict[str, Any]:
    from apps.common.models import HostIP
    from .probe_services import resolve_deploy_host_endpoint

    primary_deploy = (
        DatabaseInstanceHost.objects.filter(instance_id=master.id, is_primary=True)
        .order_by("sort_order", "id")
        .first()
    )
    if not primary_deploy:
        primary_deploy = (
            DatabaseInstanceHost.objects.filter(instance_id=master.id)
            .order_by("sort_order", "id")
            .first()
        )

    master_deploy_host_id = primary_deploy.host_id if primary_deploy else None
    same_host = master_deploy_host_id is not None and master_deploy_host_id == slave_target_host_id
    master_port = int(primary_deploy.listener_port or master.port) if primary_deploy else int(master.port)

    if same_host:
        return {
            "master_connect_host": master.connect_host,
            "master_host": "127.0.0.1",
            "master_port": master_port,
            "same_host_as_master": True,
            "master_deploy_host_id": master_deploy_host_id,
        }

    master_host = master.connect_host
    if primary_deploy:
        business_ip = (
            HostIP.objects.filter(host_id=primary_deploy.host_id, ip_type="business")
            .order_by("id")
            .values_list("ip_address", flat=True)
            .first()
        )
        if not business_ip:
            business_ip = (
                HostIP.objects.filter(host_id=primary_deploy.host_id)
                .order_by("id")
                .values_list("ip_address", flat=True)
                .first()
            )
        if business_ip and master.connect_host == business_ip:
            master_host = business_ip
        else:
            host_ip_map = {
                primary_deploy.host_id: business_ip or "",
            }
            master_host, master_port = resolve_deploy_host_endpoint(
                primary_deploy,
                host_ip_map=host_ip_map,
            )

    return {
        "master_connect_host": master.connect_host,
        "master_host": master_host,
        "master_port": int(master_port),
        "same_host_as_master": False,
        "master_deploy_host_id": master_deploy_host_id,
    }


def _load_replication_cluster_for_replica(cluster_id: int) -> DatabaseReplicationCluster:
    try:
        cluster = DatabaseReplicationCluster.objects.select_related("primary_instance").get(id=cluster_id)
    except DatabaseReplicationCluster.DoesNotExist as exc:
        raise ServiceError("复制集不存在") from exc
    if cluster.engine != "mysql" or cluster.replication_type != "mysql_replication":
        raise ServiceError("请选择 MySQL 主从复制集")
    if not cluster.primary_instance_id or not cluster.primary_instance:
        raise ServiceError("复制集未配置主实例")
    return cluster


def _validate_mysql_replica_create_body(body: dict[str, Any]) -> dict[str, Any]:
    host_id = body.get("target_host_id") or body.get("host_id")
    if not host_id:
        raise ServiceError("请选择从库部署主机")

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
    context = user_params.get("context") if isinstance(user_params.get("context"), dict) else {}
    replication_cluster_id = context.get("replication_cluster_id") or body.get("replication_cluster_id")
    if not replication_cluster_id:
        raise ServiceError("请选择复制集")

    cluster = _load_replication_cluster_for_replica(int(replication_cluster_id))
    master = cluster.primary_instance
    master_instance_id = int(context.get("master_instance_id") or master.id)
    if master_instance_id != master.id:
        raise ServiceError("主实例与复制集不一致")

    bootstrap_method = (context.get("bootstrap_method") or body.get("bootstrap_method") or "mysqldump").strip()
    if bootstrap_method not in {"mysqldump", "xtrabackup", "clone"}:
        raise ServiceError("请选择有效的全量同步方式")
    if bootstrap_method != "mysqldump":
        raise ServiceError("第一期仅支持 mysqldump 全量同步")

    enable_semi_sync = _coerce_bool(context.get("enable_semi_sync"), default=False)

    if not (master.version or "").strip():
        raise ServiceError("主库版本号为空，请先在 CMDB 维护主库版本")

    master_major = parse_mysql_major_version(master.version)
    if not master_major:
        raise ServiceError("无法解析主库 major 版本")

    profile = None
    for item in list_profiles(job_type="mysql_replica"):
        if item["profile_code"] == profile_code:
            profile = item
            break
    if not profile:
        raise ServiceError("所选版本档案无效")
    if profile.get("major_version") != master_major:
        raise ServiceError("版本档案 major 须与主库一致")

    credentials = user_params.setdefault("credentials", {})
    repl_account_id = credentials.get("repl_account_id") or body.get("repl_account_id")
    if not repl_account_id:
        raise ServiceError("请选择主库复制账号")
    try:
        repl_account = DatabaseAccount.objects.get(
            id=int(repl_account_id),
            instance_id=master.id,
            account_type="user_repl",
        )
    except DatabaseAccount.DoesNotExist as exc:
        raise ServiceError("复制账号无效或不属于主库") from exc

    dump_account_id = credentials.get("dump_account_id") or body.get("dump_account_id")
    if dump_account_id:
        try:
            dump_account = DatabaseAccount.objects.get(
                id=int(dump_account_id),
                instance_id=master.id,
                account_type=MYSQL_DBA_ACCOUNT_TYPE,
            )
        except DatabaseAccount.DoesNotExist as exc:
            raise ServiceError("全量同步账号无效或不属于主库") from exc
    else:
        dump_account = DatabaseAccount.objects.filter(
            instance_id=master.id,
            account_type=MYSQL_DBA_ACCOUNT_TYPE,
            is_default=True,
        ).order_by("id").first()
        if not dump_account:
            raise ServiceError("主库缺少默认 DBA 账号，无法执行 mysqldump")

    _fetch_mysql_master_root_account(master)

    cmdb = user_params.setdefault("cmdb", {})
    cmdb["instance_name"] = (cmdb.get("instance_name") or body.get("instance_name") or "").strip()
    if not cmdb["instance_name"]:
        raise ServiceError("请填写从库实例名称")
    if DatabaseInstance.objects.filter(instance_name=cmdb["instance_name"]).exists():
        raise ServiceError("实例名称已存在")

    port = cmdb.get("port")
    if not port:
        raise ServiceError("请填写从库端口")
    port_num = int(port)
    if port_num < 1024 or port_num > 65535:
        raise ServiceError("MySQL 端口无效")

    cmdb["connect_host"] = validate_mysql_deploy_connect_host(
        int(host_id),
        str(cmdb.get("connect_host") or ""),
    )

    db_name = (cmdb.get("db_name") or "").strip()
    endpoint_qs = DatabaseInstance.objects.filter(
        engine="mysql",
        connect_host=cmdb["connect_host"],
        port=port_num,
        db_name=db_name,
    )
    if endpoint_qs.exists():
        raise ServiceError("从库连接端点与已有实例冲突")

    master_endpoint = resolve_mysql_master_replication_endpoint(
        master=master,
        slave_target_host_id=int(host_id),
    )
    if (
        cmdb["connect_host"] == master.connect_host
        and port_num == int(master.port)
    ):
        raise ServiceError("从库连接端点不能与主库相同")
    if master_endpoint["same_host_as_master"] and port_num == int(master.port):
        raise ServiceError("同机部署时从库端口不能与主库相同")

    meta = user_params.setdefault("meta", {})
    meta["job_type"] = "mysql_replica"
    meta["version_profile_code"] = profile_code
    template_code = (meta.get("mysql_param_template_code") or body.get("mysql_param_template_code") or "").strip()
    template_title = (meta.get("mysql_param_template_title") or body.get("mysql_param_template_title") or "").strip()
    if template_code:
        meta["mysql_param_template_code"] = template_code
    if template_title:
        meta["mysql_param_template_title"] = template_title

    context.update({
        "replication_cluster_id": cluster.id,
        "master_instance_id": master.id,
        "bootstrap_method": bootstrap_method,
        "force_rebuild": bool(context.get("force_rebuild")),
        "enable_semi_sync": enable_semi_sync,
    })
    context.update(master_endpoint)
    user_params["context"] = context

    cmdb.update({
        "topology": "replication",
        "role": "slave",
        "replication_cluster_id": cluster.id,
        "master_instance_name": master.instance_name,
    })

    config = user_params.setdefault("config", {})
    config["enable_binlog"] = True
    config["enable_gtid"] = True

    credentials["repl_account_id"] = repl_account.id
    credentials["dump_account_id"] = dump_account.id

    return {
        "job_type": "mysql_replica",
        "host_id": int(host_id),
        "environment_id": int(environment_id),
        "business_id": int(business_id),
        "profile_code": profile_code,
        "user_params": user_params,
        "creator": (body.get("creator") or "").strip(),
        "remark": (body.get("remark") or "").strip(),
        "master": master,
        "repl_account": repl_account,
        "dump_account": dump_account,
    }


def _fetch_mysql_master_root_account(master: DatabaseInstance) -> DatabaseAccount:
    """读取主库实例台账中的本地 root 账号（user_adm / root@localhost）。"""
    root_account = (
        DatabaseAccount.objects.filter(
            instance_id=master.id,
            account_type=MYSQL_ROOT_ACCOUNT_TYPE,
            account_name="root",
            grant_host=MYSQL_ROOT_GRANT_HOST,
        )
        .order_by("id")
        .first()
    )
    if not root_account or not (root_account.account_pswd or "").strip():
        raise ServiceError(
            "主库缺少本地 root 账号台账（user_adm / root@localhost），无法建立复制"
        )
    return root_account


def _apply_mysql_replica_root_credentials(
    resolved: dict[str, Any],
    master: DatabaseInstance,
) -> None:
    root_account = _fetch_mysql_master_root_account(master)
    resolved.setdefault("credentials", {})
    resolved["credentials"]["root_account"] = {
        "account_name": root_account.account_name,
        "account_pswd": root_account.account_pswd,
        "grant_host": root_account.grant_host,
        "account_type": root_account.account_type,
        "source_account_id": root_account.id,
        "source_instance_id": master.id,
    }


def ensure_mysql_replica_root_credentials(job: DbDeployJob) -> None:
    """建立复制前确保 resolved_params 含主库 root 密码（兼容历史任务）。"""
    resolved = dict(job.resolved_params or {})
    credentials = resolved.get("credentials") or {}
    root_account = credentials.get("root_account") or {}
    if (root_account.get("account_pswd") or "").strip():
        return

    context = resolved.get("context") or {}
    master_id = context.get("master_instance_id")
    if not master_id:
        raise ServiceError("从库任务缺少 master_instance_id，无法解析主库 root 密码")
    master = DatabaseInstance.objects.get(id=int(master_id))
    _apply_mysql_replica_root_credentials(resolved, master)
    job.resolved_params = resolved
    job.save(update_fields=["resolved_params", "updated_at"])


_MASTER_INSTALL_KEYS = ("basedir", "datadir", "socket", "cnf_path")
_MASTER_INSTALL_DISCOVERED_SOURCES = frozenset({"sql", "process", "sql+process", "derived"})


def _master_install_is_complete(install: dict[str, Any] | None) -> bool:
    """仅当四条路径齐全且来自探测（非历史 build_mysql_install_paths 推导）时视为可复用。"""
    if not isinstance(install, dict):
        return False
    source = str(install.get("source") or "").strip()
    if source not in _MASTER_INSTALL_DISCOVERED_SOURCES:
        return False
    return all(str(install.get(key) or "").strip() for key in _MASTER_INSTALL_KEYS)


def _normalize_master_install(data: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(data, dict):
        return result
    for key in _MASTER_INSTALL_KEYS:
        value = str(data.get(key) or "").strip()
        if value:
            result[key] = value
    return result


def _merge_master_install(
    base: dict[str, Any] | None,
    *sources: dict[str, Any] | None,
) -> dict[str, str]:
    merged = _normalize_master_install(base)
    for source in sources:
        for key, value in _normalize_master_install(source).items():
            if key not in merged:
                merged[key] = value
    return merged


def _discover_mysql_master_install_via_sql(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
) -> dict[str, str]:
    """通过 DBA TCP 查询主库运行变量，获取 basedir / datadir / socket。"""
    import mysql.connector

    names = ("basedir", "datadir", "socket")
    placeholders = ", ".join(f"'{n}'" for n in names)
    conn = mysql.connector.connect(
        host=host,
        port=int(port),
        user=user,
        password=password,
        connection_timeout=10,
    )
    try:
        cursor = conn.cursor()
        cursor.execute(f"SHOW VARIABLES WHERE Variable_name IN ({placeholders})")
        rows = {str(name).lower(): str(value) for name, value in cursor.fetchall()}
    finally:
        conn.close()
    return _normalize_master_install(rows)


def _derive_cnf_path_from_socket(socket_path: str) -> str:
    """从 socket 反推 my.cnf（仅兜底，优先用进程 --defaults-file）。

    …/mysql.sock -> …/my.cnf
    """
    socket_path = (socket_path or "").strip()
    if not socket_path or "/" not in socket_path:
        return ""
    parent = socket_path.rsplit("/", 1)[0]
    return f"{parent}/my.cnf" if parent else ""


def _build_mysql_master_process_probe_script(port: int) -> str:
    """在主库部署机上按监听端口解析 mysqld 进程路径。

    主路径：cmdline --defaults-file + 读取 cnf；
    兜底：{{datadir父}}/mysql.sock、{{datadir父}}/my.cnf；socket 为 …/mysql.sock 时反推 …/my.cnf。
    """
    port_num = int(port)
    # 注意：整段脚本经 ansible -m raw -a 下发，避免嵌套引号过多；结果仅输出一行标记。
    return f"""
set -u
PORT={port_num}
run_priv() {{
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo -n "$@"
  else
    "$@"
  fi
}}
PID=""
if command -v ss >/dev/null 2>&1; then
  # 例: LISTEN ... *:3306 ... users:(("mysqld",pid=6718,fd=23))
  PID=$(run_priv ss -lntp 2>/dev/null | grep "${{PORT}}" | grep -i mysqld | head -1 | sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p' || true)
  if [ -z "$PID" ]; then
    PID=$(run_priv ss -lntp 2>/dev/null | grep -E ":${{PORT}}([[:space:]]|$)" | head -1 | sed -n 's/.*pid=\\([0-9][0-9]*\\).*/\\1/p' || true)
  fi
fi
if [ -z "$PID" ] && command -v lsof >/dev/null 2>&1; then
  PID=$(run_priv lsof -nP -iTCP:${{PORT}} -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {{print $2; exit}}' || true)
fi
if [ -z "$PID" ]; then
  for p in $(pgrep -x mysqld 2>/dev/null; pgrep -f '[m]ysqld' 2>/dev/null); do
    CMD=$(tr '\\0' ' ' < /proc/$p/cmdline 2>/dev/null || true)
    case "$CMD" in
      *"--port=${{PORT}}"*|*"--port ${{PORT}}"*) PID=$p; break ;;
    esac
  done
fi
if [ -z "$PID" ] || [ ! -r "/proc/${{PID}}/cmdline" ]; then
  echo "ERROR|未找到监听端口 ${{PORT}} 的 mysqld 进程" >&2
  exit 1
fi
CMD=$(tr '\\0' ' ' < /proc/${{PID}}/cmdline)
# --defaults-file=/data/mysql/dbPORT/etc/my.cnf （现场主路径）
CNF=$(printf '%s\\n' "$CMD" | sed -n 's/.*--[[:space:]]*defaults-file[= ]\\([^[:space:]]*\\).*/\\1/p' | head -1)
[ -z "$CNF" ] && CNF=$(printf '%s\\n' "$CMD" | sed -n 's/.*--[[:space:]]*defaults_file[= ]\\([^[:space:]]*\\).*/\\1/p' | head -1)
DATADIR=$(printf '%s\\n' "$CMD" | sed -n 's/.*--[[:space:]]*datadir[= ]\\([^[:space:]]*\\).*/\\1/p' | head -1)
SOCKET=$(printf '%s\\n' "$CMD" | sed -n 's/.*--[[:space:]]*socket[= ]\\([^[:space:]]*\\).*/\\1/p' | head -1)
BASEDIR=$(printf '%s\\n' "$CMD" | sed -n 's/.*--[[:space:]]*basedir[= ]\\([^[:space:]]*\\).*/\\1/p' | head -1)
read_cnf_key() {{
  # 优先 [mysqld]，再 grep 首条 key=（兼容 socket 写在 [client]）
  local key="$1" file="$2" val=""
  [ -n "$file" ] && [ -r "$file" ] || return 0
  val=$(awk -F= -v k="$key" '
    BEGIN {{ IGNORECASE=1 }}
    /^[[:space:]]*#/ {{ next }}
    /^[[:space:]]*\\[/ {{
      sect=$0; gsub(/[[:space:]]/, "", sect); sect=tolower(sect)
    }}
    sect == "[mysqld]" {{
      keyname=$1; gsub(/^[[:space:]]+|[[:space:]]+$/, "", keyname)
      if (tolower(keyname) == tolower(k)) {{
        v=$2; gsub(/^[[:space:]]+|[[:space:]]+$/, "", v); print v; exit
      }}
    }}
  ' "$file" || true)
  if [ -z "$val" ]; then
    val=$(grep -iE "^[[:space:]]*${{key}}[[:space:]]*=" "$file" 2>/dev/null | head -1 | cut -d= -f2- | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true)
  fi
  printf '%s' "$val"
}}
if [ -n "$CNF" ] && [ -r "$CNF" ]; then
  [ -z "$DATADIR" ] && DATADIR=$(read_cnf_key datadir "$CNF")
  [ -z "$SOCKET" ] && SOCKET=$(read_cnf_key socket "$CNF")
  [ -z "$BASEDIR" ] && BASEDIR=$(read_cnf_key basedir "$CNF")
fi
if [ -z "$BASEDIR" ]; then
  EXE=$(readlink -f "/proc/${{PID}}/exe" 2>/dev/null || true)
  if [ -z "$EXE" ]; then
    EXE=$(printf '%s\\n' "$CMD" | awk '{{print $1; exit}}')
  fi
  if [ -n "$EXE" ]; then
    case "$EXE" in
      */bin/mysqld|*/sbin/mysqld) BASEDIR=$(dirname "$(dirname "$EXE")") ;;
    esac
  fi
fi
if [ -z "$SOCKET" ] && [ -n "$DATADIR" ]; then
  ROOT=$(dirname "$DATADIR")
  if [ -S "${{ROOT}}/mysql.sock" ]; then SOCKET="${{ROOT}}/mysql.sock"; fi
fi
if [ -z "$CNF" ] && [ -n "$DATADIR" ]; then
  ROOT=$(dirname "$DATADIR")
  if [ -f "${{ROOT}}/my.cnf" ]; then CNF="${{ROOT}}/my.cnf"; fi
fi
if [ -z "$CNF" ] && [ -n "$SOCKET" ]; then
  case "$SOCKET" in
    */mysql.sock)
      PARENT=$(dirname "$SOCKET")
      [ -f "${{PARENT}}/my.cnf" ] && CNF="${{PARENT}}/my.cnf"
      ;;
  esac
fi
if [ -z "$BASEDIR" ] || [ -z "$DATADIR" ] || [ -z "$SOCKET" ] || [ -z "$CNF" ]; then
  echo "ERROR|进程探测不完整 basedir=${{BASEDIR:-}} datadir=${{DATADIR:-}} socket=${{SOCKET:-}} cnf=${{CNF:-}} cmd=${{CMD}}" >&2
  exit 1
fi
# 单行标记；Python 侧用 raw_decode 容忍 Ansible 粘连尾部
printf 'MASTER_INSTALL_JSON={{"basedir":"%s","datadir":"%s","socket":"%s","cnf_path":"%s"}}\\n' \
  "$BASEDIR" "$DATADIR" "$SOCKET" "$CNF"
""".strip()


def _parse_master_install_json_payload(payload: str) -> dict[str, Any]:
    """解析 MASTER_INSTALL_JSON 后的载荷；容忍 Ansible 粘连的尾部垃圾。"""
    text = (payload or "").strip()
    if not text:
        raise json.JSONDecodeError("empty", text, 0)
    # 优先取第一个 JSON 对象，忽略 Extra data
    decoder = json.JSONDecoder()
    try:
        data, _end = decoder.raw_decode(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text)
        if not match:
            raise
        data, _end = decoder.raw_decode(match.group(0))
    if not isinstance(data, dict):
        raise json.JSONDecodeError("not an object", text, 0)
    return data


def _discover_mysql_master_install_via_process(
    *,
    host_id: int,
    port: int,
) -> dict[str, str]:
    """SSH 主库部署机，按 mysqld 进程识别 basedir / datadir / socket / cnf_path。"""
    from apps.common.ansible_inventory import _run_ansible_raw
    from apps.common.models import Host

    try:
        host = Host.objects.get(id=host_id)
    except Host.DoesNotExist as exc:
        raise ServiceError(f"主库部署主机不存在: {host_id}") from exc

    outputs = _run_ansible_raw(
        [host_id],
        _build_mysql_master_process_probe_script(port),
        timeout=60,
    )
    stdout = outputs.get(host.hostname, "")
    if not stdout:
        err = outputs.get("__stderr__") or outputs.get("__error__") or outputs.get("__raw__") or "无输出"
        raise ServiceError(f"[{host.hostname}] 主库进程路径探测失败: {err}")

    marker = "MASTER_INSTALL_JSON="
    payload = ""
    for line in stdout.replace("\\n", "\n").splitlines():
        text = line.strip()
        if "ERROR|" in text and marker not in text:
            raise ServiceError(f"[{host.hostname}] {text.split('ERROR|', 1)[-1]}")
        if marker in text:
            payload = text.split(marker, 1)[1].strip()
            break
    if not payload and marker in stdout:
        payload = stdout.split(marker, 1)[1].strip()
    if not payload:
        raise ServiceError(
            f"[{host.hostname}] 主库进程路径探测未返回 MASTER_INSTALL_JSON: {stdout[:500]}"
        )

    try:
        data = _parse_master_install_json_payload(payload)
    except json.JSONDecodeError as exc:
        raise ServiceError(
            f"[{host.hostname}] 解析主库进程路径失败: {exc}; payload={payload[:300]}"
        ) from exc
    return _normalize_master_install(data)


def ensure_mysql_replica_master_install(job: DbDeployJob) -> None:
    """半同步等步骤需本机连主库：探测并落库 master_install（basedir/datadir/socket/cnf_path）。

    优先 DBA SQL（SHOW VARIABLES），不足时 SSH 主库按 mysqld 进程补齐；已完整则复用。
    """
    resolved = dict(job.resolved_params or {})
    context = resolved.setdefault("context", {})
    master_id = context.get("master_instance_id")
    if not master_id:
        raise ServiceError("从库任务缺少 master_instance_id，无法解析主库部署路径")

    if not context.get("master_deploy_host_id"):
        master = DatabaseInstance.objects.get(id=int(master_id))
        endpoint = resolve_mysql_master_replication_endpoint(
            master=master,
            slave_target_host_id=job.target_host_id,
        )
        if endpoint.get("master_deploy_host_id"):
            context["master_deploy_host_id"] = endpoint["master_deploy_host_id"]
        if endpoint.get("master_host") and not context.get("master_host"):
            context["master_host"] = endpoint["master_host"]
        if endpoint.get("master_port") and not context.get("master_port"):
            context["master_port"] = endpoint["master_port"]

    existing = context.get("master_install") if isinstance(context.get("master_install"), dict) else {}
    cached = _normalize_master_install(existing)
    if existing.get("source"):
        cached["source"] = str(existing["source"]).strip()
    if _master_install_is_complete(cached):
        context["master_install"] = {
            k: cached[k] for k in (*_MASTER_INSTALL_KEYS, "source") if cached.get(k)
        }
        job.resolved_params = resolved
        job.save(update_fields=["resolved_params", "updated_at"])
        return

    # 无 source 的历史推导路径不可信，重新探测（不沿用旧值）
    install: dict[str, str] = {}
    master_host = str(context.get("master_host") or "").strip()
    master_port = int(context.get("master_port") or 3306)
    master_deploy_host_id = context.get("master_deploy_host_id")
    errors: list[str] = []
    sources: list[str] = []

    credentials = resolved.get("credentials") or {}
    dump_account = credentials.get("dump_account") or {}
    dump_user = str(dump_account.get("account_name") or "").strip()
    dump_pswd = str(dump_account.get("account_pswd") or "")
    if master_host and dump_user:
        try:
            sql_install = _discover_mysql_master_install_via_sql(
                host=master_host,
                port=master_port,
                user=dump_user,
                password=dump_pswd,
            )
            if sql_install:
                install = _merge_master_install(install, sql_install)
                sources.append("sql")
        except Exception as exc:  # noqa: BLE001 — 回退进程探测
            errors.append(f"SQL探测失败: {exc}")

    # SQL 拿不到 cnf_path（无 defaults-file 变量）；缺字段时再用进程补齐
    need_process = not all(install.get(k) for k in ("basedir", "datadir", "socket", "cnf_path"))
    if need_process:
        if not master_deploy_host_id:
            if (
                install.get("basedir")
                and install.get("datadir")
                and install.get("socket")
                and not install.get("cnf_path")
            ):
                derived = _derive_cnf_path_from_socket(install["socket"])
                if derived:
                    install["cnf_path"] = derived
                    sources.append("derived")
            if not all(install.get(k) for k in _MASTER_INSTALL_KEYS):
                raise ServiceError(
                    "主库路径未探测完整，且缺少 master_deploy_host_id，无法进程探测"
                    + (f"（{'; '.join(errors)}）" if errors else "")
                )
        else:
            try:
                proc_install = _discover_mysql_master_install_via_process(
                    host_id=int(master_deploy_host_id),
                    port=master_port,
                )
                if proc_install:
                    install = _merge_master_install(install, proc_install)
                    sources.append("process")
            except ServiceError as exc:
                errors.append(str(exc))
                if (
                    install.get("basedir")
                    and install.get("datadir")
                    and install.get("socket")
                    and not install.get("cnf_path")
                ):
                    derived = _derive_cnf_path_from_socket(install["socket"])
                    if derived:
                        install["cnf_path"] = derived
                        sources.append("derived")
                if not all(install.get(k) for k in _MASTER_INSTALL_KEYS):
                    raise ServiceError(
                        "无法识别主库 basedir/datadir/socket/cnf_path: "
                        + "; ".join(errors)
                    ) from exc

    if not install.get("cnf_path") and install.get("socket"):
        derived = _derive_cnf_path_from_socket(install["socket"])
        if derived:
            install["cnf_path"] = derived
            if "derived" not in sources:
                sources.append("derived")

    if not all(install.get(k) for k in _MASTER_INSTALL_KEYS):
        missing = [k for k in _MASTER_INSTALL_KEYS if not install.get(k)]
        raise ServiceError(
            "主库路径探测不完整，缺少: "
            + ", ".join(missing)
            + (f"（{'; '.join(errors)}）" if errors else "")
        )

    if "sql" in sources and "process" in sources:
        install["source"] = "sql+process"
    elif "sql" in sources and "derived" in sources and "process" not in sources:
        install["source"] = "sql"
    elif "process" in sources:
        install["source"] = "process"
    elif "sql" in sources:
        install["source"] = "sql"
    else:
        install["source"] = "derived"

    context["master_install"] = {
        key: install[key] for key in (*_MASTER_INSTALL_KEYS, "source") if install.get(key)
    }
    job.resolved_params = resolved
    job.save(update_fields=["resolved_params", "updated_at"])


def resolve_mysql_replica_deploy_params(
    *,
    profile_code: str,
    user_params: dict[str, Any],
    host_id: int,
    deploy_job_id: int | None = None,
    master: DatabaseInstance,
    repl_account: DatabaseAccount,
    dump_account: DatabaseAccount,
) -> dict[str, Any]:
    resolved = resolve_deploy_params(
        job_type="mysql_replica",
        profile_code=profile_code,
        user_params=user_params,
        host_id=host_id,
        deploy_job_id=deploy_job_id,
    )
    endpoint = resolve_mysql_master_replication_endpoint(
        master=master,
        slave_target_host_id=host_id,
    )
    resolved.setdefault("context", {})
    resolved["context"].update(user_params.get("context") or {})
    resolved["context"].update(endpoint)
    resolved.setdefault("cmdb", {})
    resolved["cmdb"].update({
        "topology": "replication",
        "role": "slave",
        "replication_cluster_id": resolved["context"].get("replication_cluster_id"),
        "master_instance_name": master.instance_name,
    })
    resolved.setdefault("credentials", {})
    resolved["credentials"]["repl_account"] = {
        "account_name": repl_account.account_name,
        "account_pswd": repl_account.account_pswd,
        "grant_host": repl_account.grant_host,
        "account_type": repl_account.account_type,
        "source_account_id": repl_account.id,
    }
    resolved["credentials"]["dump_account"] = {
        "account_name": dump_account.account_name,
        "account_pswd": dump_account.account_pswd,
        "grant_host": dump_account.grant_host,
        "account_type": dump_account.account_type,
        "is_default": dump_account.is_default,
        "source_instance_id": master.id,
        "source_account_id": dump_account.id,
    }
    _apply_mysql_replica_root_credentials(resolved, master)
    return resolved


def create_mysql_replica_deploy_job(body: dict[str, Any]) -> dict[str, Any]:
    from apps.common.models import Host

    fields = _validate_mysql_replica_create_body(body)
    resolved = resolve_mysql_replica_deploy_params(
        profile_code=fields["profile_code"],
        user_params=fields["user_params"],
        host_id=fields["host_id"],
        master=fields["master"],
        repl_account=fields["repl_account"],
        dump_account=fields["dump_account"],
    )

    with transaction.atomic():
        Host.objects.select_for_update().get(id=fields["host_id"])
        ensure_host_deploy_lock_available(fields["host_id"])
        job = DbDeployJob.objects.create(
            job_type="mysql_replica",
            status="pending",
            target_host_id=fields["host_id"],
            environment_id=fields["environment_id"],
            business_id=fields["business_id"],
            params=fields["user_params"],
            resolved_params=resolved,
            creator=fields["creator"],
            remark=fields["remark"],
        )
        job.resolved_params = resolve_mysql_replica_deploy_params(
            profile_code=fields["profile_code"],
            user_params=fields["user_params"],
            host_id=fields["host_id"],
            deploy_job_id=job.id,
            master=fields["master"],
            repl_account=fields["repl_account"],
            dump_account=fields["dump_account"],
        )
        job.save(update_fields=["resolved_params", "updated_at"])
        for index, (step_code, step_name) in enumerate(MYSQL_REPLICA_DEPLOY_STEPS):
            DbDeployJobStep.objects.create(
                job=job,
                step_code=step_code,
                step_name=step_name,
                sort_order=index + 1,
            )

    from .deploy_tasks import run_db_deploy_job

    run_db_deploy_job.delay(job.id)
    return {
        "code": 0,
        "msg": "从库部署任务已创建",
        "data": {"job_id": job.id},
    }


def list_mysql_replica_deploy_jobs(
    *,
    page: int = 1,
    limit: int = 20,
    keyword: str = "",
) -> dict[str, Any]:
    from django.db.models import Q

    queryset = (
        DbDeployJob.objects.filter(job_type="mysql_replica")
        .select_related("target_host", "environment", "business", "instance")
        .order_by("-id")
    )
    if keyword:
        queryset = queryset.filter(
            Q(params__cmdb__instance_name__icontains=keyword)
            | Q(params__cmdb__master_instance_name__icontains=keyword)
            | Q(target_host__display_name__icontains=keyword)
            | Q(creator__icontains=keyword),
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_deploy_job(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}
