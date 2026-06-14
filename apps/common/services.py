"""apps.common 核心业务逻辑层。"""
from __future__ import annotations

import io
from typing import Any

import openpyxl
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q

from .models import (
    BatchTask,
    BatchTaskHost,
    Business,
    DataCenter,
    DockerContainer,
    Environment,
    FileDistTask,
    FileDistTaskHost,
    Host,
    HostAccount,
    HostDomainName,
    HostHardware,
    HostIP,
    HostStatus,
    HostType,
    OSType,
    ScriptCategory,
    ScriptRepository,
    VmInstance,
)
from .tasks import run_batch_task, run_file_dist_task


class ServiceError(Exception):
    """业务异常，由视图层转换为 HTTP 响应。"""

    def __init__(self, msg: str, http_status: int = 400) -> None:
        super().__init__(msg)
        self.msg = msg
        self.http_status = http_status


# ==================== 通用配置 ====================

MODEL_MAP: dict[str, type] = {
    "host-status": HostStatus,
    "host-type": HostType,
    "datacenter": DataCenter,
    "business": Business,
    "os-type": OSType,
    "environment": Environment,
}

MODEL_NAME_MAP: dict[str, str] = {
    "host-status": "主机状态",
    "host-type": "主机类型",
    "datacenter": "所属机房",
    "business": "所属业务",
    "os-type": "OS类型",
    "environment": "所属环境",
}

MODEL_CHOICES_MAP: dict[str, list[dict[str, str]]] = {
    "environment": [
        {"value": "prod", "label": "生产环境"},
        {"value": "pre", "label": "预发环境"},
        {"value": "test", "label": "测试环境"},
        {"value": "dev", "label": "开发环境"},
    ],
}


def get_config_model(model_key: str) -> tuple[type, bool]:
    if model_key not in MODEL_MAP:
        raise ServiceError("无效的模型标识")
    return MODEL_MAP[model_key], model_key in MODEL_CHOICES_MAP


def list_config_records(
    model_class: type,
    *,
    page: int,
    limit: int,
    keyword: str,
    has_choices: bool = False,
) -> dict[str, Any]:
    queryset = model_class.objects.all().order_by("-id")
    if keyword:
        queryset = queryset.filter(name__icontains=keyword)
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = []
    for obj in page_obj.object_list:
        item: dict[str, Any] = {"id": obj.id, "name": obj.name}
        if has_choices:
            item["code"] = obj.code
        data.append(item)
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_config_record(
    model_class: type,
    body: dict[str, Any],
    *,
    has_choices: bool = False,
) -> dict[str, Any]:
    if has_choices:
        code = body.get("code", "").strip()
        name = body.get("name", "").strip()
        if not code or not name:
            raise ServiceError("参数不完整")
        if model_class.objects.filter(code=code).exists():
            raise ServiceError("编码已存在")
        obj = model_class.objects.create(code=code, name=name)
        return {
            "code": 0,
            "msg": "添加成功",
            "data": {"id": obj.id, "code": obj.code, "name": obj.name},
        }
    name = body.get("name", "").strip()
    if not name:
        raise ServiceError("名称不能为空")
    if model_class.objects.filter(name=name).exists():
        raise ServiceError("名称已存在")
    obj = model_class.objects.create(name=name)
    return {"code": 0, "msg": "添加成功", "data": {"id": obj.id, "name": obj.name}}


def update_config_record(
    model_class: type,
    body: dict[str, Any],
    *,
    has_choices: bool = False,
) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    if has_choices:
        code = body.get("code", "").strip()
        name = body.get("name", "").strip()
        if not code or not name:
            raise ServiceError("参数不完整")
        if model_class.objects.filter(code=code).exclude(id=obj_id).exists():
            raise ServiceError("编码已存在")
        try:
            obj = model_class.objects.get(id=obj_id)
        except model_class.DoesNotExist as exc:
            raise ServiceError("记录不存在", 404) from exc
        obj.code = code
        obj.name = name
        obj.save()
        return {
            "code": 0,
            "msg": "更新成功",
            "data": {"id": obj.id, "code": obj.code, "name": obj.name},
        }
    name = body.get("name", "").strip()
    if not name:
        raise ServiceError("参数不完整")
    if model_class.objects.filter(name=name).exclude(id=obj_id).exists():
        raise ServiceError("名称已存在")
    try:
        obj = model_class.objects.get(id=obj_id)
    except model_class.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    obj.name = name
    obj.save()
    return {"code": 0, "msg": "更新成功", "data": {"id": obj.id, "name": obj.name}}


def delete_config_record(model_class: type, obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        model_class.objects.get(id=obj_id).delete()
    except model_class.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== 公共工具 ====================

def get_hosts_with_first_ip() -> list[dict[str, Any]]:
    ip_map: dict[int, str] = {}
    for ip in HostIP.objects.order_by("host_id", "id").values("host_id", "ip_address"):
        if ip["host_id"] not in ip_map:
            ip_map[ip["host_id"]] = ip["ip_address"]
    host_data = []
    for host in Host.objects.values("id", "display_name", "hostname"):
        host["first_ip"] = ip_map.get(host["id"], "")
        host_data.append(host)
    return host_data


def get_host_form_options() -> dict[str, list[dict[str, Any]]]:
    return {
        "host_types": list(HostType.objects.values("id", "name")),
        "host_statuses": list(HostStatus.objects.values("id", "name")),
        "datacenters": list(DataCenter.objects.values("id", "name")),
        "businesses": list(Business.objects.values("id", "name")),
        "os_types": list(OSType.objects.values("id", "name")),
    }


# ==================== 脚本分类 ====================

def build_category_tree(
    categories: list[ScriptCategory],
    parent_id: int | None = None,
    level: int = 0,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for cat in categories:
        if cat.parent_id == parent_id:
            result.append({
                "id": cat.id,
                "name": cat.name,
                "level": level,
                "display": ("　" * level) + cat.name,
            })
            result.extend(build_category_tree(categories, cat.id, level + 1))
    return result


def get_script_category_tree() -> list[dict[str, Any]]:
    categories = list(ScriptCategory.objects.all().order_by("sort_order", "id"))
    return build_category_tree(categories)


def serialize_category(obj: ScriptCategory) -> dict[str, Any]:
    return {
        "id": obj.id,
        "name": obj.name,
        "parent_id": obj.parent_id,
        "parent__name": obj.parent.name if obj.parent_id else "",
        "sort_order": obj.sort_order,
        "remark": obj.remark,
    }


def list_script_categories(*, page: int, limit: int, keyword: str) -> dict[str, Any]:
    queryset = ScriptCategory.objects.select_related("parent").order_by("sort_order", "id")
    if keyword:
        queryset = queryset.filter(name__icontains=keyword)
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_category(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_script_category(body: dict[str, Any]) -> dict[str, Any]:
    name = body.get("name", "").strip()
    if not name:
        raise ServiceError("分类名称不能为空")
    ScriptCategory.objects.create(
        name=name,
        parent_id=body.get("parent_id") or None,
        sort_order=int(body.get("sort_order", 0)),
        remark=body.get("remark", "").strip(),
    )
    return {"code": 0, "msg": "添加成功"}


def update_script_category(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = ScriptCategory.objects.get(id=obj_id)
    except ScriptCategory.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    parent_id = body.get("parent_id") or None
    if parent_id and int(parent_id) == obj.id:
        raise ServiceError("上级分类不能选择自身")
    obj.name = body.get("name", "").strip()
    obj.parent_id = parent_id
    obj.sort_order = int(body.get("sort_order", obj.sort_order))
    obj.remark = body.get("remark", "").strip()
    obj.save()
    return {"code": 0, "msg": "更新成功"}


def delete_script_category(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        ScriptCategory.objects.get(id=obj_id).delete()
    except ScriptCategory.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== 脚本仓库 ====================

SCRIPT_TYPE_LABELS = {
    "shell": "Shell",
    "python": "Python",
    "sql": "SQL",
    "bat": "Batch",
    "perl": "Perl",
    "other": "其他",
}
SCRIPT_STATUS_LABELS = {"enabled": "启用", "disabled": "停用"}


def serialize_script(obj: ScriptRepository) -> dict[str, Any]:
    return {
        "id": obj.id,
        "name": obj.name,
        "category_id": obj.category_id,
        "category__name": obj.category.name if obj.category_id else "",
        "script_type": obj.script_type,
        "script_type__display": SCRIPT_TYPE_LABELS.get(obj.script_type, obj.script_type),
        "content": obj.content,
        "description": obj.description,
        "version": obj.version,
        "status": obj.status,
        "status__display": SCRIPT_STATUS_LABELS.get(obj.status, obj.status),
        "creator": obj.creator,
        "remark": obj.remark,
    }


def _collect_category_descendant_ids(category_id: int) -> list[int]:
    cat_ids = [category_id]
    child_ids = list(ScriptCategory.objects.filter(parent_id=category_id).values_list("id", flat=True))
    while child_ids:
        cat_ids.extend(child_ids)
        child_ids = list(ScriptCategory.objects.filter(parent_id__in=child_ids).values_list("id", flat=True))
    return cat_ids


def list_script_repositories(
    *,
    page: int,
    limit: int,
    keyword: str,
    category_id: str,
    script_type: str,
) -> dict[str, Any]:
    queryset = ScriptRepository.objects.select_related("category").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(name__icontains=keyword)
            | Q(description__icontains=keyword)
            | Q(creator__icontains=keyword)
        )
    if category_id:
        queryset = queryset.filter(category_id__in=_collect_category_descendant_ids(int(category_id)))
    if script_type:
        queryset = queryset.filter(script_type=script_type)
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_script(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_script_repository(body: dict[str, Any]) -> dict[str, Any]:
    name = body.get("name", "").strip()
    if not name:
        raise ServiceError("脚本名称不能为空")
    ScriptRepository.objects.create(
        name=name,
        category_id=body.get("category_id") or None,
        script_type=body.get("script_type", "shell"),
        content=body.get("content", ""),
        description=body.get("description", "").strip(),
        version=body.get("version", "1.0").strip(),
        status=body.get("status", "enabled"),
        creator=body.get("creator", "").strip(),
        remark=body.get("remark", "").strip(),
    )
    return {"code": 0, "msg": "添加成功"}


def update_script_repository(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = ScriptRepository.objects.get(id=obj_id)
    except ScriptRepository.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    obj.name = body.get("name", "").strip()
    obj.category_id = body.get("category_id") or None
    obj.script_type = body.get("script_type", obj.script_type)
    obj.content = body.get("content", obj.content)
    obj.description = body.get("description", "").strip()
    obj.version = body.get("version", "1.0").strip()
    obj.status = body.get("status", obj.status)
    obj.creator = body.get("creator", "").strip()
    obj.remark = body.get("remark", "").strip()
    obj.save()
    return {"code": 0, "msg": "更新成功"}


def delete_script_repository(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        ScriptRepository.objects.get(id=obj_id).delete()
    except ScriptRepository.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== 批量执行 ====================

BATCH_TASK_STATUS_LABELS = {
    "pending": "待执行",
    "running": "执行中",
    "completed": "已完成",
    "failed": "执行失败",
}
BATCH_HOST_STATUS_LABELS = {
    "pending": "等待中",
    "running": "执行中",
    "success": "成功",
    "failed": "失败",
}


def serialize_batch_task(obj: BatchTask) -> dict[str, Any]:
    return {
        "id": obj.id,
        "script_id": obj.script_id,
        "script__name": obj.script.name,
        "script_type__display": SCRIPT_TYPE_LABELS.get(obj.script.script_type, obj.script.script_type),
        "status": obj.status,
        "status__display": BATCH_TASK_STATUS_LABELS.get(obj.status, obj.status),
        "forks": obj.forks,
        "total_count": obj.total_count,
        "success_count": obj.success_count,
        "fail_count": obj.fail_count,
        "creator": obj.creator,
        "remark": obj.remark,
        "created_at": obj.created_at.isoformat() if obj.created_at else "",
        "started_at": obj.started_at.isoformat() if obj.started_at else "",
        "finished_at": obj.finished_at.isoformat() if obj.finished_at else "",
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else "",
    }


def serialize_batch_host(obj: BatchTaskHost) -> dict[str, Any]:
    first_ip = HostIP.objects.filter(host_id=obj.host_id).order_by("id").first()
    return {
        "id": obj.id,
        "host_id": obj.host_id,
        "host__display": str(obj.host),
        "host_ip": first_ip.ip_address if first_ip else "",
        "status": obj.status,
        "status__display": BATCH_HOST_STATUS_LABELS.get(obj.status, obj.status),
        "output": obj.output,
        "duration": obj.duration,
        "started_at": obj.started_at.isoformat() if obj.started_at else "",
        "finished_at": obj.finished_at.isoformat() if obj.finished_at else "",
    }


def list_batch_tasks(*, page: int, limit: int, keyword: str) -> dict[str, Any]:
    queryset = BatchTask.objects.select_related("script").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(script__name__icontains=keyword) | Q(creator__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_batch_task(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_batch_task(body: dict[str, Any]) -> dict[str, Any]:
    script_id = body.get("script_id")
    host_ids = body.get("host_ids", [])
    if not script_id:
        raise ServiceError("请选择脚本")
    if not host_ids:
        raise ServiceError("请选择目标主机")
    try:
        script = ScriptRepository.objects.get(id=script_id, status="enabled")
    except ScriptRepository.DoesNotExist as exc:
        raise ServiceError("脚本不存在或已停用") from exc
    task = BatchTask.objects.create(
        script=script,
        forks=int(body.get("forks", 5) or 5),
        total_count=len(host_ids),
        creator=body.get("creator", "").strip(),
        remark=body.get("remark", "").strip(),
    )
    for host_id in host_ids:
        BatchTaskHost.objects.create(task=task, host_id=host_id)
    run_batch_task.delay(task.id)
    return {"code": 0, "msg": "任务已启动", "data": {"task_id": task.id}}


def delete_batch_task(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        BatchTask.objects.get(id=obj_id).delete()
    except BatchTask.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


def get_batch_task_detail(task_id: int) -> dict[str, Any]:
    try:
        task = BatchTask.objects.select_related("script").get(id=task_id)
    except BatchTask.DoesNotExist as exc:
        raise ServiceError("任务不存在", 404) from exc
    task_data = serialize_batch_task(task)
    host_list = BatchTaskHost.objects.filter(task_id=task_id).select_related("host").order_by("-finished_at")
    task_data["hosts"] = [serialize_batch_host(item) for item in host_list]
    return {"code": 0, "msg": "", "data": task_data}


# ==================== 文件分发 ====================

FILE_DIST_SOURCE_LABELS = {"local": "本地上传", "remote": "远程主机"}
FILE_DIST_STATUS_LABELS = {
    "pending": "待执行",
    "running": "执行中",
    "completed": "已完成",
    "failed": "执行失败",
}
FILE_DIST_HOST_STATUS_LABELS = {
    "pending": "等待中",
    "running": "执行中",
    "success": "成功",
    "failed": "失败",
}


def serialize_file_dist_task(obj: FileDistTask) -> dict[str, Any]:
    return {
        "id": obj.id,
        "name": obj.name,
        "source_type": obj.source_type,
        "source_type__display": FILE_DIST_SOURCE_LABELS.get(obj.source_type, obj.source_type),
        "source_host__display": str(obj.source_host) if obj.source_host else "",
        "source_path": obj.source_path,
        "dest_path": obj.dest_path,
        "status": obj.status,
        "status__display": FILE_DIST_STATUS_LABELS.get(obj.status, obj.status),
        "total_count": obj.total_count,
        "success_count": obj.success_count,
        "fail_count": obj.fail_count,
        "creator": obj.creator,
        "remark": obj.remark,
        "created_at": obj.created_at.isoformat() if obj.created_at else "",
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else "",
    }


def serialize_file_dist_host(obj: FileDistTaskHost) -> dict[str, Any]:
    return {
        "id": obj.id,
        "host_id": obj.host_id,
        "host__display": str(obj.host),
        "status": obj.status,
        "status__display": FILE_DIST_HOST_STATUS_LABELS.get(obj.status, obj.status),
        "output": obj.output,
        "started_at": obj.started_at.isoformat() if obj.started_at else "",
        "finished_at": obj.finished_at.isoformat() if obj.finished_at else "",
    }


def list_file_dist_tasks(*, page: int, limit: int, keyword: str) -> dict[str, Any]:
    queryset = FileDistTask.objects.select_related("source_host").order_by("-id")
    if keyword:
        queryset = queryset.filter(Q(name__icontains=keyword) | Q(creator__icontains=keyword))
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_file_dist_task(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_file_dist_task(form_data: dict[str, Any]) -> dict[str, Any]:
    name = form_data.get("name", "").strip()
    source_type = form_data.get("source_type", "local")
    host_ids_raw = form_data.get("host_ids", "")
    dest_path = form_data.get("dest_path", "").strip()
    dest_owner = form_data.get("dest_owner", "root").strip() or "root"
    dest_group = form_data.get("dest_group", "root").strip() or "root"
    dest_mode = form_data.get("dest_mode", "0644").strip() or "0644"
    backup = form_data.get("backup") is True
    creator = form_data.get("creator", "").strip()
    remark = form_data.get("remark", "").strip()
    local_file = form_data.get("local_file")

    if not name:
        raise ServiceError("请输入任务名称")
    if not host_ids_raw:
        raise ServiceError("请选择目标主机")
    if not dest_path:
        raise ServiceError("请输入目标路径")
    try:
        host_ids = [int(item) for item in host_ids_raw.split(",") if item.strip()]
    except ValueError as exc:
        raise ServiceError("主机ID格式错误") from exc
    if not host_ids:
        raise ServiceError("请选择目标主机")
    if source_type == "local" and not local_file:
        raise ServiceError("请上传文件")

    source_host = None
    source_path = ""
    if source_type == "remote":
        source_host_id = form_data.get("source_host_id", "")
        source_path = form_data.get("source_path", "").strip()
        if not source_host_id or not source_path:
            raise ServiceError("远程来源需选择源主机并填写源路径")
        try:
            source_host = Host.objects.get(id=int(source_host_id))
        except (ValueError, Host.DoesNotExist) as exc:
            raise ServiceError("源主机不存在") from exc

    task = FileDistTask.objects.create(
        name=name,
        source_type=source_type,
        local_file=local_file,
        source_host=source_host,
        source_path=source_path,
        dest_path=dest_path,
        dest_owner=dest_owner,
        dest_group=dest_group,
        dest_mode=dest_mode,
        backup=backup,
        total_count=len(host_ids),
        creator=creator,
        remark=remark,
    )
    for host_id in host_ids:
        FileDistTaskHost.objects.create(task=task, host_id=host_id)
    run_file_dist_task.delay(task.id)
    return {"code": 0, "msg": "任务已启动", "data": {"task_id": task.id}}


def delete_file_dist_task(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        FileDistTask.objects.get(id=obj_id).delete()
    except FileDistTask.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


def get_file_dist_task_detail(task_id: int) -> dict[str, Any]:
    try:
        task = FileDistTask.objects.select_related("source_host").get(id=task_id)
    except FileDistTask.DoesNotExist as exc:
        raise ServiceError("任务不存在", 404) from exc
    task_data = serialize_file_dist_task(task)
    host_list = FileDistTaskHost.objects.filter(task_id=task_id).select_related("host").order_by("id")
    task_data["hosts"] = [serialize_file_dist_host(item) for item in host_list]
    return {"code": 0, "msg": "", "data": task_data}


# ==================== 主机维护 ====================

def serialize_host(host: Host) -> dict[str, Any]:
    ips = list(host.ips.values("id", "ip_address", "ip_type", "nic"))
    accounts = list(host.accounts.values("id", "account_type", "account_name", "account_pswd"))
    admins = [item for item in accounts if item["account_type"] == "adm"]
    admin_accounts_display = " / ".join(
        f'{item["account_name"]} / {item["account_pswd"]}' for item in admins
    )
    return {
        "id": host.id,
        "display_name": host.display_name,
        "hostname": host.hostname,
        "ssh_port": host.ssh_port,
        "host_type_id": host.host_type_id,
        "host_type__name": host.host_type.name,
        "host_status_id": host.host_status_id,
        "host_status__name": host.host_status.name,
        "datacenter_id": host.datacenter_id,
        "datacenter__name": host.datacenter.name,
        "business_id": host.business_id,
        "business__name": host.business.name,
        "os_type_id": host.os_type_id,
        "os_type__name": host.os_type.name,
        "os_version": host.os_version,
        "remark": host.remark,
        "first_ip": ips[0]["ip_address"] if ips else "",
        "first_ip_type": ips[0]["ip_type"] if ips else "",
        "ip_count": len(ips),
        "account_count": len(accounts),
        "admin_accounts_display": admin_accounts_display,
        "ips": ips,
        "accounts": accounts,
    }


def list_hosts(*, page: int, limit: int, keyword: str) -> dict[str, Any]:
    queryset = Host.objects.select_related(
        "host_type", "host_status", "datacenter", "business", "os_type",
    ).order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(display_name__icontains=keyword) | Q(hostname__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_host(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def export_hosts_xlsx(keyword: str = "") -> bytes:
    queryset = Host.objects.select_related(
        "host_type", "host_status", "datacenter", "business", "os_type",
    ).order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(display_name__icontains=keyword) | Q(hostname__icontains=keyword)
        )
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "主机列表"
    worksheet.append([
        "ID", "显示名", "主机名", "SSH端口", "IP地址", "管理员账号",
        "主机类型", "主机状态", "所属机房", "所属业务", "OS类型", "OS版本", "备注",
    ])
    for host_obj in queryset:
        row = serialize_host(host_obj)
        worksheet.append([
            row["id"], row["display_name"], row["hostname"], row["ssh_port"],
            row["first_ip"], row["admin_accounts_display"],
            row["host_type__name"], row["host_status__name"], row["datacenter__name"],
            row["business__name"], row["os_type__name"], row["os_version"], row["remark"],
        ])
    output = io.BytesIO()
    workbook.save(output)
    output.seek(0)
    return output.getvalue()


def _validate_host_ip_account(ips: list[dict[str, Any]], accounts: list[dict[str, Any]]) -> str | None:
    if ips and ips[0].get("ip_type", "") != "business":
        return "第一个主机IP必须为业务IP"
    adm_count = sum(1 for item in accounts if item.get("account_type") == "adm")
    if adm_count == 0:
        return "至少需要一个管理员账号"
    if adm_count > 1:
        return "只能保留一个管理员账号，其余请设为普通用户"
    return None


def _sync_ips(host: Host, ip_list: list[dict[str, Any]]) -> None:
    existing_ids = set(HostIP.objects.filter(host=host).values_list("id", flat=True))
    keep_ids: set[int] = set()
    for ip_data in ip_list:
        ip_address = ip_data.get("ip_address", "").strip()
        if not ip_address:
            continue
        ip_id = ip_data.get("id")
        defaults = {
            "ip_address": ip_address,
            "ip_type": ip_data.get("ip_type", "business"),
            "nic": ip_data.get("nic", "").strip(),
        }
        if ip_id and ip_id in existing_ids:
            HostIP.objects.filter(id=ip_id, host=host).update(**defaults)
            keep_ids.add(ip_id)
        else:
            obj = HostIP.objects.create(host=host, **defaults)
            keep_ids.add(obj.id)
    HostIP.objects.filter(host=host).exclude(id__in=keep_ids).delete()


def _sync_accounts(host: Host, acct_list: list[dict[str, Any]]) -> None:
    existing_ids = set(HostAccount.objects.filter(host=host).values_list("id", flat=True))
    keep_ids: set[int] = set()
    for acct_data in acct_list:
        account_name = acct_data.get("account_name", "").strip()
        if not account_name:
            continue
        acct_id = acct_data.get("id")
        defaults = {
            "account_type": acct_data.get("account_type", "std"),
            "account_name": account_name,
            "account_pswd": acct_data.get("account_pswd", ""),
        }
        if acct_id and acct_id in existing_ids:
            HostAccount.objects.filter(id=acct_id, host=host).update(**defaults)
            keep_ids.add(acct_id)
        else:
            obj = HostAccount.objects.create(host=host, **defaults)
            keep_ids.add(obj.id)
    HostAccount.objects.filter(host=host).exclude(id__in=keep_ids).delete()


def create_host(body: dict[str, Any]) -> dict[str, Any]:
    err = _validate_host_ip_account(body.get("ips", []), body.get("accounts", []))
    if err:
        raise ServiceError(err)
    host_obj = Host.objects.create(
        display_name=body.get("display_name", "").strip(),
        hostname=body.get("hostname", "").strip(),
        ssh_port=body.get("ssh_port", 22),
        host_type_id=body.get("host_type_id"),
        host_status_id=body.get("host_status_id"),
        datacenter_id=body.get("datacenter_id"),
        business_id=body.get("business_id"),
        os_type_id=body.get("os_type_id"),
        os_version=body.get("os_version", "").strip(),
        remark=body.get("remark", "").strip(),
    )
    _sync_ips(host_obj, body.get("ips", []))
    _sync_accounts(host_obj, body.get("accounts", []))
    return {"code": 0, "msg": "添加成功"}


def update_host(body: dict[str, Any]) -> dict[str, Any]:
    host_id = body.get("id")
    if not host_id:
        raise ServiceError("参数不完整")
    try:
        host_obj = Host.objects.get(id=host_id)
    except Host.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    err = _validate_host_ip_account(body.get("ips", []), body.get("accounts", []))
    if err:
        raise ServiceError(err)
    host_obj.display_name = body.get("display_name", "").strip()
    host_obj.hostname = body.get("hostname", "").strip()
    host_obj.ssh_port = body.get("ssh_port", 22)
    host_obj.host_type_id = body.get("host_type_id")
    host_obj.host_status_id = body.get("host_status_id")
    host_obj.datacenter_id = body.get("datacenter_id")
    host_obj.business_id = body.get("business_id")
    host_obj.os_type_id = body.get("os_type_id")
    host_obj.os_version = body.get("os_version", "").strip()
    host_obj.remark = body.get("remark", "").strip()
    host_obj.save()
    _sync_ips(host_obj, body.get("ips", []))
    _sync_accounts(host_obj, body.get("accounts", []))
    return {"code": 0, "msg": "更新成功"}


def delete_host(host_id: Any) -> dict[str, Any]:
    if not host_id:
        raise ServiceError("参数不完整")
    try:
        Host.objects.get(id=host_id).delete()
    except Host.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== 主机硬件 ====================

def serialize_hardware(obj: HostHardware) -> dict[str, Any]:
    return {
        "id": obj.id,
        "host_id": obj.host_id,
        "host__display": str(obj.host),
        "cpu_model": obj.cpu_model,
        "cpu_cores": obj.cpu_cores,
        "memory": obj.memory,
        "disk": obj.disk,
        "disk_detail": obj.disk_detail,
        "raid": obj.raid,
        "vender": obj.vender,
        "sn": obj.sn,
        "remark": obj.remark,
        "purchase_date": obj.purchase_date.isoformat() if obj.purchase_date else "",
        "warranty_date": obj.warranty_date.isoformat() if obj.warranty_date else "",
    }


def list_hardware(*, page: int, limit: int, keyword: str) -> dict[str, Any]:
    queryset = HostHardware.objects.select_related("host").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(host__display_name__icontains=keyword)
            | Q(host__hostname__icontains=keyword)
            | Q(cpu_model__icontains=keyword)
            | Q(vender__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_hardware(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_hardware(body: dict[str, Any]) -> dict[str, Any]:
    if not body.get("host_id"):
        raise ServiceError("请选择关联主机")
    HostHardware.objects.create(
        host_id=body["host_id"],
        cpu_model=body.get("cpu_model", "").strip(),
        cpu_cores=body.get("cpu_cores"),
        memory=body.get("memory", "").strip(),
        disk=body.get("disk", "").strip(),
        disk_detail=body.get("disk_detail", "").strip(),
        raid=body.get("raid", "").strip(),
        vender=body.get("vender", "").strip(),
        sn=body.get("sn", "").strip(),
        remark=body.get("remark", "").strip(),
        purchase_date=body.get("purchase_date") or None,
        warranty_date=body.get("warranty_date") or None,
    )
    return {"code": 0, "msg": "添加成功"}


def update_hardware(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = HostHardware.objects.get(id=obj_id)
    except HostHardware.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    obj.host_id = body.get("host_id", obj.host_id)
    obj.cpu_model = body.get("cpu_model", "").strip()
    obj.cpu_cores = body.get("cpu_cores")
    obj.memory = body.get("memory", "").strip()
    obj.disk = body.get("disk", "").strip()
    obj.disk_detail = body.get("disk_detail", "").strip()
    obj.raid = body.get("raid", "").strip()
    obj.vender = body.get("vender", "").strip()
    obj.sn = body.get("sn", "").strip()
    obj.remark = body.get("remark", "").strip()
    obj.purchase_date = body.get("purchase_date") or None
    obj.warranty_date = body.get("warranty_date") or None
    obj.save()
    return {"code": 0, "msg": "更新成功"}


def delete_hardware(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        HostHardware.objects.get(id=obj_id).delete()
    except HostHardware.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== 主机域名 ====================

DOMAIN_TYPE_LABELS = {
    "business": "业务域名",
    "admin": "管理后台",
    "test": "测试域名",
}
DOMAIN_STATUS_LABELS = {
    "normal": "正常",
    "stop": "已停用",
    "expired": "已过期",
    "pending": "待备案",
}


def serialize_domain(obj: HostDomainName) -> dict[str, Any]:
    return {
        "id": obj.id,
        "host_id": obj.host_id,
        "host__display": str(obj.host),
        "domain_name": obj.domain_name,
        "domain_type": obj.domain_type,
        "domain_type__display": DOMAIN_TYPE_LABELS.get(obj.domain_type, obj.domain_type),
        "dns_server": obj.dns_server,
        "resolve_ip": obj.resolve_ip,
        "status": obj.status,
        "status__display": DOMAIN_STATUS_LABELS.get(obj.status, obj.status),
        "is_https": obj.is_https,
        "ssl_expire_time": obj.ssl_expire_time.isoformat() if obj.ssl_expire_time else "",
        "domain_expire_time": obj.domain_expire_time.isoformat() if obj.domain_expire_time else "",
        "business_id": obj.business_id,
        "business__name": obj.business.name if obj.business_id else "",
        "registrant": obj.registrant,
        "remark": obj.remark,
    }


def list_domains(*, page: int, limit: int, keyword: str) -> dict[str, Any]:
    queryset = HostDomainName.objects.select_related("host", "business").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(domain_name__icontains=keyword)
            | Q(host__display_name__icontains=keyword)
            | Q(host__hostname__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_domain(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_domain(body: dict[str, Any]) -> dict[str, Any]:
    if not body.get("domain_name", "").strip():
        raise ServiceError("域名不能为空")
    HostDomainName.objects.create(
        host_id=body.get("host_id"),
        domain_name=body["domain_name"].strip(),
        domain_type=body.get("domain_type", "business"),
        dns_server=body.get("dns_server", "").strip(),
        resolve_ip=body.get("resolve_ip", "").strip(),
        status=body.get("status", "normal"),
        is_https=body.get("is_https", False),
        ssl_expire_time=body.get("ssl_expire_time") or None,
        domain_expire_time=body.get("domain_expire_time") or None,
        business_id=body.get("business_id") or None,
        registrant=body.get("registrant", "").strip(),
        remark=body.get("remark", "").strip(),
    )
    return {"code": 0, "msg": "添加成功"}


def update_domain(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = HostDomainName.objects.get(id=obj_id)
    except HostDomainName.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    obj.host_id = body.get("host_id", obj.host_id)
    obj.domain_name = body.get("domain_name", "").strip()
    obj.domain_type = body.get("domain_type", obj.domain_type)
    obj.dns_server = body.get("dns_server", "").strip()
    obj.resolve_ip = body.get("resolve_ip", "").strip()
    obj.status = body.get("status", obj.status)
    obj.is_https = body.get("is_https", obj.is_https)
    obj.ssl_expire_time = body.get("ssl_expire_time") or None
    obj.domain_expire_time = body.get("domain_expire_time") or None
    obj.business_id = body.get("business_id") or None
    obj.registrant = body.get("registrant", "").strip()
    obj.remark = body.get("remark", "").strip()
    obj.save()
    return {"code": 0, "msg": "更新成功"}


def delete_domain(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        HostDomainName.objects.get(id=obj_id).delete()
    except HostDomainName.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== 虚拟机实例 ====================

VIRT_PLATFORM_LABELS = {
    "vmware": "VMware",
    "kvm": "KVM",
    "proxmox": "Proxmox",
    "other": "其他",
}


def serialize_vm(obj: VmInstance) -> dict[str, Any]:
    host_ips = list(obj.host.ips.all())
    parent_host_ips = list(obj.parent_host.ips.all()) if obj.parent_host_id else []
    return {
        "id": obj.id,
        "host_id": obj.host_id,
        "host__display": str(obj.host),
        "vm_ip": host_ips[0].ip_address if host_ips else "",
        "virt_platform": obj.virt_platform,
        "virt_platform__display": VIRT_PLATFORM_LABELS.get(obj.virt_platform, obj.virt_platform),
        "vm_uuid": obj.vm_uuid,
        "parent_host_id": obj.parent_host_id,
        "parent_host__display": str(obj.parent_host) if obj.parent_host_id else "",
        "parent_host_ip": parent_host_ips[0].ip_address if parent_host_ips else "",
        "resource_pool": obj.resource_pool,
        "cluster_name": obj.cluster_name,
        "cpu_quota": obj.cpu_quota,
        "mem_quota": obj.mem_quota,
        "disk_quota": obj.disk_quota,
        "snapshot_count": obj.snapshot_count,
        "has_snapshot": obj.has_snapshot,
        "auto_start": obj.auto_start,
        "remark": obj.remark,
    }


def list_vm_instances(*, page: int, limit: int, keyword: str) -> dict[str, Any]:
    queryset = VmInstance.objects.select_related("host", "parent_host").prefetch_related(
        Prefetch("host__ips", queryset=HostIP.objects.order_by("id")),
        Prefetch("parent_host__ips", queryset=HostIP.objects.order_by("id")),
    ).order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(host__display_name__icontains=keyword)
            | Q(host__hostname__icontains=keyword)
            | Q(vm_uuid__icontains=keyword)
            | Q(resource_pool__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_vm(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_vm_instance(body: dict[str, Any]) -> dict[str, Any]:
    if not body.get("host_id"):
        raise ServiceError("请选择关联主机")
    VmInstance.objects.create(
        host_id=body["host_id"],
        virt_platform=body.get("virt_platform", "kvm"),
        vm_uuid=body.get("vm_uuid", "").strip(),
        parent_host_id=body.get("parent_host_id") or None,
        resource_pool=body.get("resource_pool", "").strip(),
        cluster_name=body.get("cluster_name", "").strip(),
        cpu_quota=body.get("cpu_quota", "").strip(),
        mem_quota=body.get("mem_quota", "").strip(),
        disk_quota=body.get("disk_quota", "").strip(),
        snapshot_count=body.get("snapshot_count", 0),
        has_snapshot=body.get("has_snapshot", False),
        auto_start=body.get("auto_start", False),
        remark=body.get("remark", "").strip(),
    )
    return {"code": 0, "msg": "添加成功"}


def update_vm_instance(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = VmInstance.objects.get(id=obj_id)
    except VmInstance.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    obj.host_id = body.get("host_id", obj.host_id)
    obj.virt_platform = body.get("virt_platform", obj.virt_platform)
    obj.vm_uuid = body.get("vm_uuid", "").strip()
    obj.parent_host_id = body.get("parent_host_id") or None
    obj.resource_pool = body.get("resource_pool", "").strip()
    obj.cluster_name = body.get("cluster_name", "").strip()
    obj.cpu_quota = body.get("cpu_quota", "").strip()
    obj.mem_quota = body.get("mem_quota", "").strip()
    obj.disk_quota = body.get("disk_quota", "").strip()
    obj.snapshot_count = body.get("snapshot_count", obj.snapshot_count)
    obj.has_snapshot = body.get("has_snapshot", obj.has_snapshot)
    obj.auto_start = body.get("auto_start", obj.auto_start)
    obj.remark = body.get("remark", "").strip()
    obj.save()
    return {"code": 0, "msg": "更新成功"}


def delete_vm_instance(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        VmInstance.objects.get(id=obj_id).delete()
    except VmInstance.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== Docker 容器 ====================

CONTAINER_STATUS_LABELS = {
    "running": "运行中",
    "stopped": "已停止",
    "exited": "已退出",
    "error": "异常",
}
NETWORK_MODE_LABELS = {
    "bridge": "桥接模式",
    "host": "主机模式",
    "none": "无网络",
    "custom": "自定义网络",
}
RESTART_POLICY_LABELS = {
    "no": "不重启",
    "always": "总是重启",
    "on-failure": "异常重启",
}


def serialize_container(obj: DockerContainer) -> dict[str, Any]:
    return {
        "id": obj.id,
        "host_id": obj.host_id,
        "host__display": str(obj.host),
        "business_id": obj.business_id,
        "business__name": obj.business.name if obj.business_id else "",
        "container_name": obj.container_name,
        "container_id": obj.container_id,
        "image_name": obj.image_name,
        "image_repository": obj.image_repository,
        "container_ip": obj.container_ip,
        "port_mapping": obj.port_mapping,
        "network_mode": obj.network_mode,
        "network_mode__display": NETWORK_MODE_LABELS.get(obj.network_mode, obj.network_mode),
        "docker_network": obj.docker_network,
        "volume_mount": obj.volume_mount,
        "cpu_limit": obj.cpu_limit,
        "mem_limit": obj.mem_limit,
        "command": obj.command,
        "env_list": obj.env_list,
        "auto_start": obj.auto_start,
        "restart_policy": obj.restart_policy,
        "restart_policy__display": RESTART_POLICY_LABELS.get(obj.restart_policy, obj.restart_policy),
        "started_at": obj.started_at.isoformat() if obj.started_at else "",
        "owner": obj.owner,
        "status": obj.status,
        "status__display": CONTAINER_STATUS_LABELS.get(obj.status, obj.status),
        "remark": obj.remark,
    }


def list_containers(*, page: int, limit: int, keyword: str) -> dict[str, Any]:
    queryset = DockerContainer.objects.select_related("host", "business").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(container_name__icontains=keyword)
            | Q(host__display_name__icontains=keyword)
            | Q(host__hostname__icontains=keyword)
            | Q(image_name__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_container(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_container(body: dict[str, Any]) -> dict[str, Any]:
    if not body.get("container_name", "").strip():
        raise ServiceError("容器名称不能为空")
    DockerContainer.objects.create(
        host_id=body.get("host_id"),
        business_id=body.get("business_id") or None,
        container_name=body["container_name"].strip(),
        container_id=body.get("container_id", "").strip(),
        image_name=body.get("image_name", "").strip(),
        image_repository=body.get("image_repository", "").strip(),
        container_ip=body.get("container_ip", "").strip(),
        port_mapping=body.get("port_mapping", "").strip(),
        network_mode=body.get("network_mode", "bridge"),
        docker_network=body.get("docker_network", "").strip(),
        volume_mount=body.get("volume_mount", "").strip(),
        cpu_limit=body.get("cpu_limit", "").strip(),
        mem_limit=body.get("mem_limit", "").strip(),
        command=body.get("command", "").strip(),
        env_list=body.get("env_list", "").strip(),
        auto_start=body.get("auto_start", False),
        restart_policy=body.get("restart_policy", "no"),
        started_at=body.get("started_at") or None,
        owner=body.get("owner", "").strip(),
        status=body.get("status", "running"),
        remark=body.get("remark", "").strip(),
    )
    return {"code": 0, "msg": "添加成功"}


def update_container(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DockerContainer.objects.get(id=obj_id)
    except DockerContainer.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    obj.host_id = body.get("host_id", obj.host_id)
    obj.business_id = body.get("business_id") or None
    obj.container_name = body.get("container_name", "").strip()
    obj.container_id = body.get("container_id", "").strip()
    obj.image_name = body.get("image_name", "").strip()
    obj.image_repository = body.get("image_repository", "").strip()
    obj.container_ip = body.get("container_ip", "").strip()
    obj.port_mapping = body.get("port_mapping", "").strip()
    obj.network_mode = body.get("network_mode", obj.network_mode)
    obj.docker_network = body.get("docker_network", "").strip()
    obj.volume_mount = body.get("volume_mount", "").strip()
    obj.cpu_limit = body.get("cpu_limit", "").strip()
    obj.mem_limit = body.get("mem_limit", "").strip()
    obj.command = body.get("command", "").strip()
    obj.env_list = body.get("env_list", "").strip()
    obj.auto_start = body.get("auto_start", obj.auto_start)
    obj.restart_policy = body.get("restart_policy", obj.restart_policy)
    obj.started_at = body.get("started_at") or None
    obj.owner = body.get("owner", "").strip()
    obj.status = body.get("status", obj.status)
    obj.remark = body.get("remark", "").strip()
    obj.save()
    return {"code": 0, "msg": "更新成功"}


def delete_container(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        DockerContainer.objects.get(id=obj_id).delete()
    except DockerContainer.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== 网页终端 ====================

def get_web_terminal_context(host_id: int) -> dict[str, Any] | None:
    try:
        host_obj = Host.objects.get(id=host_id)
    except Host.DoesNotExist:
        return None
    first_ip = HostIP.objects.filter(host_id=host_id).order_by("id").first()
    if not first_ip:
        return None
    account = (
        HostAccount.objects.filter(host_id=host_id, account_type="adm").order_by("id").first()
        or HostAccount.objects.filter(host_id=host_id).order_by("id").first()
    )
    return {
        "host_id": host_obj.id,
        "host_display": str(host_obj),
        "hostname": host_obj.hostname,
        "ip": first_ip.ip_address,
        "user": account.account_name if account else "root",
    }


def list_web_terminal_hosts() -> dict[str, Any]:
    host_list = []
    for host_item in get_hosts_with_first_ip():
        host_list.append({
            "id": host_item["id"],
            "display_name": host_item["display_name"] or host_item["hostname"],
            "hostname": host_item["hostname"],
            "first_ip": host_item["first_ip"],
        })
    return {"code": 0, "data": host_list}
