import io
import json

import openpyxl
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import (
    BatchTask, BatchTaskHost, Business, DataCenter, DockerContainer,
    Environment, FileDistTask, FileDistTaskHost, Host, HostAccount,
    HostDomainName, HostHardware, HostIP, HostStatus, HostType, OSType,
    ScriptCategory, ScriptRepository, VmInstance,
)
from .tasks import run_batch_task, run_file_dist_task

MODEL_MAP = {
    "host-status": HostStatus,
    "host-type": HostType,
    "datacenter": DataCenter,
    "business": Business,
    "os-type": OSType,
    "environment": Environment,
}

MODEL_NAME_MAP = {
    "host-status": "主机状态",
    "host-type": "主机类型",
    "datacenter": "所属机房",
    "business": "所属业务",
    "os-type": "OS类型",
    "environment": "所属环境",
}

MODEL_CHOICES_MAP = {
    "environment": [
        {"value": "prod", "label": "生产环境"},
        {"value": "pre", "label": "预发环境"},
        {"value": "test", "label": "测试环境"},
        {"value": "dev", "label": "开发环境"},
    ],
}


def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        if not username or not password:
            return render(request, "login.html", {"error": "请输入用户名和密码"})

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect("dashboard")
        return render(request, "login.html", {"error": "用户名或密码错误"})

    return render(request, "login.html")


def logout_view(request):
    logout(request)
    return redirect("login")


@login_required
def dashboard_view(request):
    return render(request, "dashboard.html")


# ===== ScriptCategory 脚本分类 =====

def _serialize_category(obj):
    return {
        "id": obj.id,
        "name": obj.name,
        "parent_id": obj.parent_id,
        "parent__name": obj.parent.name if obj.parent_id else "",
        "sort_order": obj.sort_order,
        "remark": obj.remark,
    }


def _build_category_tree(categories, parent_id=None, level=0):
    """按层级展开分类列表，用于前端 select 展示"""
    result = []
    for cat in categories:
        if cat.parent_id == parent_id:
            result.append({
                "id": cat.id,
                "name": cat.name,
                "level": level,
                "display": ("　" * level) + cat.name,
            })
            result.extend(_build_category_tree(categories, cat.id, level + 1))
    return result


@login_required
def script_category_list_view(request):
    categories = list(ScriptCategory.objects.all().order_by("sort_order", "id"))
    context = {
        "categories_tree": json.dumps(_build_category_tree(categories)),
    }
    return render(request, "common/script_category_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def script_category_api_view(request):
    if request.method == "GET":
        return _script_category_list(request)
    elif request.method == "POST":
        return _script_category_create(request)
    elif request.method == "PUT":
        return _script_category_update(request)
    elif request.method == "DELETE":
        return _script_category_delete(request)


def _script_category_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()
    queryset = ScriptCategory.objects.select_related("parent").order_by("sort_order", "id")
    if keyword:
        queryset = queryset.filter(name__icontains=keyword)
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [_serialize_category(c) for c in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


def _script_category_create(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    name = body.get("name", "").strip()
    if not name:
        return JsonResponse({"code": 1, "msg": "分类名称不能为空"}, status=400)
    parent_id = body.get("parent_id") or None
    ScriptCategory.objects.create(
        name=name,
        parent_id=parent_id,
        sort_order=int(body.get("sort_order", 0)),
        remark=body.get("remark", "").strip(),
    )
    return JsonResponse({"code": 0, "msg": "添加成功"})


def _script_category_update(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        obj = ScriptCategory.objects.get(id=obj_id)
    except ScriptCategory.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)
    obj.name = body.get("name", "").strip()
    parent_id = body.get("parent_id") or None
    # 防止将自身或后代设为上级分类
    if parent_id and int(parent_id) == obj.id:
        return JsonResponse({"code": 1, "msg": "上级分类不能选择自身"}, status=400)
    obj.parent_id = parent_id
    obj.sort_order = int(body.get("sort_order", obj.sort_order))
    obj.remark = body.get("remark", "").strip()
    obj.save()
    return JsonResponse({"code": 0, "msg": "更新成功"})


def _script_category_delete(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        obj = ScriptCategory.objects.get(id=obj_id)
        obj.delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except ScriptCategory.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


# ===== ScriptRepository 脚本仓库 =====

SCRIPT_TYPE_LABELS = {"shell": "Shell", "python": "Python", "sql": "SQL", "bat": "Batch", "perl": "Perl", "other": "其他"}
SCRIPT_STATUS_LABELS = {"enabled": "启用", "disabled": "停用"}


def _serialize_script(obj):
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


@login_required
def script_repository_list_view(request):
    categories = list(ScriptCategory.objects.all().order_by("sort_order", "id"))
    context = {
        "script_types": json.dumps([{"value": k, "label": v} for k, v in SCRIPT_TYPE_LABELS.items()]),
        "categories_tree": json.dumps(_build_category_tree(categories)),
    }
    return render(request, "common/script_repository_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def script_repository_api_view(request):
    if request.method == "GET":
        return _script_repository_list(request)
    elif request.method == "POST":
        return _script_repository_create(request)
    elif request.method == "PUT":
        return _script_repository_update(request)
    elif request.method == "DELETE":
        return _script_repository_delete(request)


def _script_repository_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()
    category_id = request.GET.get("category_id", "").strip()
    script_type = request.GET.get("script_type", "").strip()
    queryset = ScriptRepository.objects.select_related("category").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(name__icontains=keyword) |
            Q(description__icontains=keyword) |
            Q(creator__icontains=keyword)
        )
    if category_id:
        cid = int(category_id)
        # 收集该分类及其所有子孙分类的 ID
        cat_ids = [cid]
        child_ids = list(ScriptCategory.objects.filter(parent_id=cid).values_list("id", flat=True))
        while child_ids:
            cat_ids.extend(child_ids)
            child_ids = list(ScriptCategory.objects.filter(parent_id__in=child_ids).values_list("id", flat=True))
        queryset = queryset.filter(category_id__in=cat_ids)
    if script_type:
        queryset = queryset.filter(script_type=script_type)
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [_serialize_script(s) for s in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


def _script_repository_create(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    name = body.get("name", "").strip()
    if not name:
        return JsonResponse({"code": 1, "msg": "脚本名称不能为空"}, status=400)
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
    return JsonResponse({"code": 0, "msg": "添加成功"})


def _script_repository_update(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        obj = ScriptRepository.objects.get(id=obj_id)
    except ScriptRepository.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)
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
    return JsonResponse({"code": 0, "msg": "更新成功"})


def _script_repository_delete(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        ScriptRepository.objects.get(id=obj_id).delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except ScriptRepository.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


# ===== BatchTask 批量执行 =====

BATCH_TASK_STATUS_LABELS = {"pending": "待执行", "running": "执行中", "completed": "已完成", "failed": "执行失败"}
BATCH_HOST_STATUS_LABELS = {"pending": "等待中", "running": "执行中", "success": "成功", "failed": "失败"}


def _serialize_batch_task(obj):
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


def _serialize_batch_host(obj):
    from .models import HostIP
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


@login_required
def batch_execute_list_view(request):
    # 取每台主机的第一个 IP
    ip_qs = HostIP.objects.order_by("host_id", "id").values("host_id", "ip_address")
    ip_map = {}
    for ip in ip_qs:
        if ip["host_id"] not in ip_map:
            ip_map[ip["host_id"]] = ip["ip_address"]
    host_data = []
    for h in Host.objects.values("id", "display_name", "hostname"):
        h["first_ip"] = ip_map.get(h["id"], "")
        host_data.append(h)
    scripts = list(ScriptRepository.objects.filter(status="enabled").values("id", "name", "script_type"))
    context = {
        "hosts": json.dumps(host_data),
        "scripts": json.dumps(scripts),
    }
    return render(request, "common/batch_execute_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def batch_execute_api_view(request):
    if request.method == "GET":
        return _batch_task_list(request)
    elif request.method == "POST":
        return _batch_task_create(request)
    elif request.method == "DELETE":
        return _batch_task_delete(request)


def _batch_task_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()
    queryset = BatchTask.objects.select_related("script").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(script__name__icontains=keyword) |
            Q(creator__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [_serialize_batch_task(t) for t in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


def _batch_task_create(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    script_id = body.get("script_id")
    host_ids = body.get("host_ids", [])
    if not script_id:
        return JsonResponse({"code": 1, "msg": "请选择脚本"}, status=400)
    if not host_ids:
        return JsonResponse({"code": 1, "msg": "请选择目标主机"}, status=400)
    try:
        script = ScriptRepository.objects.get(id=script_id, status="enabled")
    except ScriptRepository.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "脚本不存在或已停用"}, status=400)
    forks = int(body.get("forks", 5) or 5)
    task = BatchTask.objects.create(
        script=script,
        forks=forks,
        total_count=len(host_ids),
        creator=body.get("creator", "").strip(),
        remark=body.get("remark", "").strip(),
    )
    for hid in host_ids:
        BatchTaskHost.objects.create(task=task, host_id=hid)
    # 触发异步执行
    run_batch_task.delay(task.id)
    return JsonResponse({"code": 0, "msg": "任务已启动", "data": {"task_id": task.id}})


def _batch_task_delete(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        BatchTask.objects.get(id=obj_id).delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except BatchTask.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


@login_required
def batch_execute_detail_api(request, task_id):
    """查询任务详情及各主机执行结果"""
    try:
        task = BatchTask.objects.select_related("script").get(id=task_id)
    except BatchTask.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "任务不存在"}, status=404)
    task_data = _serialize_batch_task(task)
    host_list = BatchTaskHost.objects.filter(task_id=task_id).select_related("host").order_by("-finished_at")
    task_data["hosts"] = [_serialize_batch_host(h) for h in host_list]
    return JsonResponse({"code": 0, "msg": "", "data": task_data})


# ==================== 文件分发 ====================

FILE_DIST_SOURCE_LABELS = {"local": "本地上传", "remote": "远程主机"}
FILE_DIST_STATUS_LABELS = {"pending": "待执行", "running": "执行中", "completed": "已完成", "failed": "执行失败"}
FILE_DIST_HOST_STATUS_LABELS = {"pending": "等待中", "running": "执行中", "success": "成功", "failed": "失败"}


def _serialize_file_dist_task(obj):
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


def _serialize_file_dist_host(obj):
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


@login_required
def file_dist_list_view(request):
    ip_qs = HostIP.objects.order_by("host_id", "id").values("host_id", "ip_address")
    ip_map = {}
    for ip in ip_qs:
        if ip["host_id"] not in ip_map:
            ip_map[ip["host_id"]] = ip["ip_address"]
    host_data = []
    for h in Host.objects.values("id", "display_name", "hostname"):
        h["first_ip"] = ip_map.get(h["id"], "")
        host_data.append(h)
    context = {"hosts": json.dumps(host_data)}
    return render(request, "common/file_dist_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def file_dist_api_view(request):
    if request.method == "GET":
        return _file_dist_list(request)
    elif request.method == "POST":
        return _file_dist_create(request)
    elif request.method == "DELETE":
        return _file_dist_delete(request)


def _file_dist_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()
    queryset = FileDistTask.objects.select_related("source_host").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(name__icontains=keyword) | Q(creator__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [_serialize_file_dist_task(t) for t in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


def _file_dist_create(request):
    name = request.POST.get("name", "").strip()
    source_type = request.POST.get("source_type", "local")
    host_ids_raw = request.POST.get("host_ids", "")
    dest_path = request.POST.get("dest_path", "").strip()
    dest_owner = request.POST.get("dest_owner", "root").strip() or "root"
    dest_group = request.POST.get("dest_group", "root").strip() or "root"
    dest_mode = request.POST.get("dest_mode", "0644").strip() or "0644"
    backup = request.POST.get("backup") == "1"
    creator = request.POST.get("creator", "").strip()
    remark = request.POST.get("remark", "").strip()

    if not name:
        return JsonResponse({"code": 1, "msg": "请输入任务名称"}, status=400)
    if not host_ids_raw:
        return JsonResponse({"code": 1, "msg": "请选择目标主机"}, status=400)
    if not dest_path:
        return JsonResponse({"code": 1, "msg": "请输入目标路径"}, status=400)

    try:
        host_ids = [int(x) for x in host_ids_raw.split(",") if x.strip()]
    except ValueError:
        return JsonResponse({"code": 1, "msg": "主机ID格式错误"}, status=400)
    if not host_ids:
        return JsonResponse({"code": 1, "msg": "请选择目标主机"}, status=400)

    local_file = request.FILES.get("local_file")
    if source_type == "local" and not local_file:
        return JsonResponse({"code": 1, "msg": "请上传文件"}, status=400)

    source_host = None
    source_path = ""
    if source_type == "remote":
        source_host_id = request.POST.get("source_host_id", "")
        source_path = request.POST.get("source_path", "").strip()
        if not source_host_id or not source_path:
            return JsonResponse({"code": 1, "msg": "远程来源需选择源主机并填写源路径"}, status=400)
        try:
            source_host = Host.objects.get(id=int(source_host_id))
        except (ValueError, Host.DoesNotExist):
            return JsonResponse({"code": 1, "msg": "源主机不存在"}, status=400)

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
    for hid in host_ids:
        FileDistTaskHost.objects.create(task=task, host_id=hid)

    run_file_dist_task.delay(task.id)
    return JsonResponse({"code": 0, "msg": "任务已启动", "data": {"task_id": task.id}})


def _file_dist_delete(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        FileDistTask.objects.get(id=obj_id).delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except FileDistTask.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


@login_required
def file_dist_detail_api(request, task_id):
    try:
        task = FileDistTask.objects.select_related("source_host").get(id=task_id)
    except FileDistTask.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "任务不存在"}, status=404)
    task_data = _serialize_file_dist_task(task)
    host_list = FileDistTaskHost.objects.filter(task_id=task_id).select_related("host").order_by("id")
    task_data["hosts"] = [_serialize_file_dist_host(h) for h in host_list]
    return JsonResponse({"code": 0, "msg": "", "data": task_data})


@ensure_csrf_cookie
@login_required
def config_list_view(request, model_key):
    if model_key not in MODEL_MAP:
        return redirect("dashboard")
    context = {
        "model_key": model_key,
        "model_name": MODEL_NAME_MAP[model_key],
    }
    if model_key in MODEL_CHOICES_MAP:
        context["choices_json"] = json.dumps(MODEL_CHOICES_MAP[model_key])
    return render(request, "common/list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def config_api_view(request, model_key):
    if model_key not in MODEL_MAP:
        return JsonResponse({"code": 1, "msg": "无效的模型标识"}, status=400)

    model_class = MODEL_MAP[model_key]
    has_choices = model_key in MODEL_CHOICES_MAP

    if request.method == "GET":
        return _list_records(request, model_class, has_choices)
    elif request.method == "POST":
        return _create_record(request, model_class, has_choices)
    elif request.method == "PUT":
        return _update_record(request, model_class, has_choices)
    elif request.method == "DELETE":
        return _delete_record(request, model_class)


def _list_records(request, model_class, has_choices=False):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()

    queryset = model_class.objects.all().order_by("-id")
    if keyword:
        queryset = queryset.filter(name__icontains=keyword)

    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)

    data = []
    for obj in page_obj.object_list:
        item = {"id": obj.id, "name": obj.name}
        if has_choices:
            item["code"] = obj.code
        data.append(item)
    return JsonResponse({
        "code": 0,
        "msg": "",
        "count": paginator.count,
        "data": data,
    })


def _create_record(request, model_class, has_choices=False):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)

    if has_choices:
        code = body.get("code", "").strip()
        name = body.get("name", "").strip()
        if not code or not name:
            return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
        if model_class.objects.filter(code=code).exists():
            return JsonResponse({"code": 1, "msg": "编码已存在"}, status=400)
        obj = model_class.objects.create(code=code, name=name)
        return JsonResponse({
            "code": 0, "msg": "添加成功",
            "data": {"id": obj.id, "code": obj.code, "name": obj.name},
        })
    else:
        name = body.get("name", "").strip()
        if not name:
            return JsonResponse({"code": 1, "msg": "名称不能为空"}, status=400)
        if model_class.objects.filter(name=name).exists():
            return JsonResponse({"code": 1, "msg": "名称已存在"}, status=400)
        obj = model_class.objects.create(name=name)
        return JsonResponse({
            "code": 0, "msg": "添加成功",
            "data": {"id": obj.id, "name": obj.name},
        })


def _update_record(request, model_class, has_choices=False):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)

    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)

    if has_choices:
        code = body.get("code", "").strip()
        name = body.get("name", "").strip()
        if not code or not name:
            return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
        if model_class.objects.filter(code=code).exclude(id=obj_id).exists():
            return JsonResponse({"code": 1, "msg": "编码已存在"}, status=400)
        try:
            obj = model_class.objects.get(id=obj_id)
            obj.code = code
            obj.name = name
            obj.save()
            return JsonResponse({
                "code": 0, "msg": "更新成功",
                "data": {"id": obj.id, "code": obj.code, "name": obj.name},
            })
        except model_class.DoesNotExist:
            return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)
    else:
        name = body.get("name", "").strip()
        if not name:
            return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
        if model_class.objects.filter(name=name).exclude(id=obj_id).exists():
            return JsonResponse({"code": 1, "msg": "名称已存在"}, status=400)
        try:
            obj = model_class.objects.get(id=obj_id)
            obj.name = name
            obj.save()
            return JsonResponse({
                "code": 0, "msg": "更新成功",
                "data": {"id": obj.id, "name": obj.name},
            })
        except model_class.DoesNotExist:
            return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


def _delete_record(request, model_class):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)

    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)

    try:
        obj = model_class.objects.get(id=obj_id)
        obj.delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except model_class.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


# ==================== 主机维护 ====================

@login_required
def host_list_view(request):
    context = {
        "host_types": list(HostType.objects.values("id", "name")),
        "host_statuses": list(HostStatus.objects.values("id", "name")),
        "datacenters": list(DataCenter.objects.values("id", "name")),
        "businesses": list(Business.objects.values("id", "name")),
        "os_types": list(OSType.objects.values("id", "name")),
    }
    return render(request, "common/host_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def host_api_view(request):
    if request.method == "GET":
        return _host_list(request)
    elif request.method == "POST":
        return _host_create(request)
    elif request.method == "PUT":
        return _host_update(request)
    elif request.method == "DELETE":
        return _host_delete(request)


def _serialize_host(host):
    ips = list(host.ips.values("id", "ip_address", "ip_type", "nic"))
    accounts = list(host.accounts.values("id", "account_type", "account_name", "account_pswd"))
    admins = [a for a in accounts if a["account_type"] == "adm"]
    admin_accounts_display = " / ".join(
        f'{a["account_name"]} / {a["account_pswd"]}' for a in admins
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


def _host_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()

    queryset = Host.objects.select_related(
        "host_type", "host_status", "datacenter", "business", "os_type",
    ).order_by("-id")

    if keyword:
        queryset = queryset.filter(
            Q(display_name__icontains=keyword) |
            Q(hostname__icontains=keyword)
        )

    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)

    data = [_serialize_host(h) for h in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


@login_required
def host_export_view(request):
    keyword = request.GET.get("keyword", "").strip()
    queryset = Host.objects.select_related(
        "host_type", "host_status", "datacenter", "business", "os_type",
    ).order_by("-id")

    if keyword:
        queryset = queryset.filter(
            Q(display_name__icontains=keyword) |
            Q(hostname__icontains=keyword)
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "主机列表"

    headers = ["ID", "显示名", "主机名", "SSH端口", "IP地址", "管理员账号",
               "主机类型", "主机状态", "所属机房", "所属业务", "OS类型", "OS版本", "备注"]
    ws.append(headers)

    for host in queryset:
        d = _serialize_host(host)
        ws.append([
            d["id"], d["display_name"], d["hostname"], d["ssh_port"],
            d["first_ip"], d["admin_accounts_display"],
            d["host_type__name"], d["host_status__name"], d["datacenter__name"],
            d["business__name"], d["os_type__name"], d["os_version"], d["remark"],
        ])

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    response = HttpResponse(
        output,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="host_list.xlsx"'
    return response


def _validate_host_ip_account(ips, accounts):
    """验证主机 IP 和账号规则"""
    if ips and ips[0].get("ip_type", "") != "business":
        return "第一个主机IP必须为业务IP"
    adm_count = sum(1 for a in accounts if a.get("account_type") == "adm")
    if adm_count == 0:
        return "至少需要一个管理员账号"
    if adm_count > 1:
        return "只能保留一个管理员账号，其余请设为普通用户"
    return None


def _host_create(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)

    err = _validate_host_ip_account(body.get("ips", []), body.get("accounts", []))
    if err:
        return JsonResponse({"code": 1, "msg": err}, status=400)

    host = Host.objects.create(
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
    _sync_ips(host, body.get("ips", []))
    _sync_accounts(host, body.get("accounts", []))
    return JsonResponse({"code": 0, "msg": "添加成功"})


def _host_update(request):
    try:
        body = json.loads(request.body)
        host_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)

    if not host_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)

    try:
        host = Host.objects.get(id=host_id)
    except Host.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)

    err = _validate_host_ip_account(body.get("ips", []), body.get("accounts", []))
    if err:
        return JsonResponse({"code": 1, "msg": err}, status=400)

    host.display_name = body.get("display_name", "").strip()
    host.hostname = body.get("hostname", "").strip()
    host.ssh_port = body.get("ssh_port", 22)
    host.host_type_id = body.get("host_type_id")
    host.host_status_id = body.get("host_status_id")
    host.datacenter_id = body.get("datacenter_id")
    host.business_id = body.get("business_id")
    host.os_type_id = body.get("os_type_id")
    host.os_version = body.get("os_version", "").strip()
    host.remark = body.get("remark", "").strip()
    host.save()

    _sync_ips(host, body.get("ips", []))
    _sync_accounts(host, body.get("accounts", []))
    return JsonResponse({"code": 0, "msg": "更新成功"})


def _host_delete(request):
    try:
        body = json.loads(request.body)
        host_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)

    if not host_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)

    try:
        Host.objects.get(id=host_id).delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except Host.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


def _sync_ips(host, ip_list):
    existing_ids = set(HostIP.objects.filter(host=host).values_list("id", flat=True))
    keep_ids = set()
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


def _sync_accounts(host, acct_list):
    existing_ids = set(HostAccount.objects.filter(host=host).values_list("id", flat=True))
    keep_ids = set()
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


# ==================== 主机硬件 ====================

def _serialize_hardware(obj):
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


@login_required
def hardware_list_view(request):
    context = {"hosts": list(Host.objects.values("id", "display_name", "hostname"))}
    return render(request, "common/hardware_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def hardware_api_view(request):
    if request.method == "GET":
        return _hardware_list(request)
    elif request.method == "POST":
        return _hardware_create(request)
    elif request.method == "PUT":
        return _hardware_update(request)
    elif request.method == "DELETE":
        return _hardware_delete(request)


def _hardware_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()
    queryset = HostHardware.objects.select_related("host").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(host__display_name__icontains=keyword) |
            Q(host__hostname__icontains=keyword) |
            Q(cpu_model__icontains=keyword) |
            Q(vender__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [_serialize_hardware(h) for h in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


def _hardware_create(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not body.get("host_id"):
        return JsonResponse({"code": 1, "msg": "请选择关联主机"}, status=400)
    obj = HostHardware.objects.create(
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
    return JsonResponse({"code": 0, "msg": "添加成功"})


def _hardware_update(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        obj = HostHardware.objects.get(id=obj_id)
    except HostHardware.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)
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
    return JsonResponse({"code": 0, "msg": "更新成功"})


def _hardware_delete(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        HostHardware.objects.get(id=obj_id).delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except HostHardware.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


# ==================== 主机域名 ====================

DOMAIN_TYPE_LABELS = {"business": "业务域名", "admin": "管理后台", "test": "测试域名"}
DOMAIN_STATUS_LABELS = {"normal": "正常", "stop": "已停用", "expired": "已过期", "pending": "待备案"}


def _serialize_domain(obj):
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


@login_required
def domain_list_view(request):
    context = {
        "hosts": list(Host.objects.values("id", "display_name", "hostname")),
        "businesses": list(Business.objects.values("id", "name")),
    }
    return render(request, "common/domain_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def domain_api_view(request):
    if request.method == "GET":
        return _domain_list(request)
    elif request.method == "POST":
        return _domain_create(request)
    elif request.method == "PUT":
        return _domain_update(request)
    elif request.method == "DELETE":
        return _domain_delete(request)


def _domain_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()
    queryset = HostDomainName.objects.select_related("host", "business").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(domain_name__icontains=keyword) |
            Q(host__display_name__icontains=keyword) |
            Q(host__hostname__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [_serialize_domain(d) for d in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


def _domain_create(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not body.get("domain_name", "").strip():
        return JsonResponse({"code": 1, "msg": "域名不能为空"}, status=400)
    obj = HostDomainName.objects.create(
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
    return JsonResponse({"code": 0, "msg": "添加成功"})


def _domain_update(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        obj = HostDomainName.objects.get(id=obj_id)
    except HostDomainName.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)
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
    return JsonResponse({"code": 0, "msg": "更新成功"})


def _domain_delete(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        HostDomainName.objects.get(id=obj_id).delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except HostDomainName.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


# ==================== 虚拟机实例 ====================

VIRT_PLATFORM_LABELS = {"vmware": "VMware", "kvm": "KVM", "proxmox": "Proxmox", "other": "其他"}


def _serialize_vm(obj):
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


@login_required
def vm_list_view(request):
    context = {"hosts": list(Host.objects.values("id", "display_name", "hostname"))}
    return render(request, "common/vm_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def vm_api_view(request):
    if request.method == "GET":
        return _vm_list(request)
    elif request.method == "POST":
        return _vm_create(request)
    elif request.method == "PUT":
        return _vm_update(request)
    elif request.method == "DELETE":
        return _vm_delete(request)


def _vm_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()
    queryset = VmInstance.objects.select_related("host", "parent_host").prefetch_related(
        Prefetch("host__ips", queryset=HostIP.objects.order_by("id")),
        Prefetch("parent_host__ips", queryset=HostIP.objects.order_by("id")),
    ).order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(host__display_name__icontains=keyword) |
            Q(host__hostname__icontains=keyword) |
            Q(vm_uuid__icontains=keyword) |
            Q(resource_pool__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [_serialize_vm(v) for v in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


def _vm_create(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not body.get("host_id"):
        return JsonResponse({"code": 1, "msg": "请选择关联主机"}, status=400)
    obj = VmInstance.objects.create(
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
    return JsonResponse({"code": 0, "msg": "添加成功"})


def _vm_update(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        obj = VmInstance.objects.get(id=obj_id)
    except VmInstance.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)
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
    return JsonResponse({"code": 0, "msg": "更新成功"})


def _vm_delete(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        VmInstance.objects.get(id=obj_id).delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except VmInstance.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


# ==================== Docker容器 ====================

CONTAINER_STATUS_LABELS = {"running": "运行中", "stopped": "已停止", "exited": "已退出", "error": "异常"}
NETWORK_MODE_LABELS = {"bridge": "桥接模式", "host": "主机模式", "none": "无网络", "custom": "自定义网络"}
RESTART_POLICY_LABELS = {"no": "不重启", "always": "总是重启", "on-failure": "异常重启"}


def _serialize_container(obj):
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


@login_required
def container_list_view(request):
    context = {
        "hosts": list(Host.objects.values("id", "display_name", "hostname")),
        "businesses": list(Business.objects.values("id", "name")),
    }
    return render(request, "common/container_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def container_api_view(request):
    if request.method == "GET":
        return _container_list(request)
    elif request.method == "POST":
        return _container_create(request)
    elif request.method == "PUT":
        return _container_update(request)
    elif request.method == "DELETE":
        return _container_delete(request)


def _container_list(request):
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 20))
    keyword = request.GET.get("keyword", "").strip()
    queryset = DockerContainer.objects.select_related("host", "business").order_by("-id")
    if keyword:
        queryset = queryset.filter(
            Q(container_name__icontains=keyword) |
            Q(host__display_name__icontains=keyword) |
            Q(host__hostname__icontains=keyword) |
            Q(image_name__icontains=keyword)
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [_serialize_container(c) for c in page_obj.object_list]
    return JsonResponse({"code": 0, "msg": "", "count": paginator.count, "data": data})


def _container_create(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not body.get("container_name", "").strip():
        return JsonResponse({"code": 1, "msg": "容器名称不能为空"}, status=400)
    obj = DockerContainer.objects.create(
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
    return JsonResponse({"code": 0, "msg": "添加成功"})


def _container_update(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        obj = DockerContainer.objects.get(id=obj_id)
    except DockerContainer.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)
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
    return JsonResponse({"code": 0, "msg": "更新成功"})


def _container_delete(request):
    try:
        body = json.loads(request.body)
        obj_id = body.get("id")
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    if not obj_id:
        return JsonResponse({"code": 1, "msg": "参数不完整"}, status=400)
    try:
        DockerContainer.objects.get(id=obj_id).delete()
        return JsonResponse({"code": 0, "msg": "删除成功"})
    except DockerContainer.DoesNotExist:
        return JsonResponse({"code": 1, "msg": "记录不存在"}, status=404)


# ==================== 网页终端 (WebSSH) ====================


@login_required
def web_terminal_list_view(request):
    """网页终端 - 主机选择页面"""
    return render(request, "common/web_terminal_list.html")


@login_required
def web_terminal_page_view(request, host_id):
    """网页终端 - 终端页面"""
    # 获取主机 SSH 连接信息
    try:
        host = Host.objects.get(id=host_id)
    except Host.DoesNotExist:
        return redirect("web_terminal_list")

    first_ip = HostIP.objects.filter(host_id=host_id).order_by("id").first()
    if not first_ip:
        return redirect("web_terminal_list")

    account = (
        HostAccount.objects.filter(host_id=host_id, account_type="adm").order_by("id").first()
        or HostAccount.objects.filter(host_id=host_id).order_by("id").first()
    )

    context = {
        "host_id": host.id,
        "host_display": str(host),
        "hostname": host.hostname,
        "ip": first_ip.ip_address,
        "user": account.account_name if account else "root",
    }
    return render(request, "common/web_terminal.html", context)


@login_required
def web_terminal_hosts_api(request):
    """网页终端 - 主机列表 JSON API"""
    ip_qs = HostIP.objects.order_by("host_id", "id").values("host_id", "ip_address")
    ip_map = {}
    for ip in ip_qs:
        if ip["host_id"] not in ip_map:
            ip_map[ip["host_id"]] = ip["ip_address"]

    host_list = []
    for h in Host.objects.values("id", "display_name", "hostname"):
        host_list.append({
            "id": h["id"],
            "display_name": h["display_name"] or h["hostname"],
            "hostname": h["hostname"],
            "first_ip": ip_map.get(h["id"], ""),
        })

    return JsonResponse({"code": 0, "data": host_list})
