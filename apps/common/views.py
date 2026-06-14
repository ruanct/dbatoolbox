import json
from typing import Any, Callable

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import Business, Host, ScriptRepository
from .services import (
    MODEL_CHOICES_MAP,
    MODEL_MAP,
    MODEL_NAME_MAP,
    SCRIPT_TYPE_LABELS,
    ServiceError,
    create_batch_task,
    create_config_record,
    create_container,
    create_domain,
    create_file_dist_task,
    create_hardware,
    create_host,
    create_script_category,
    create_script_repository,
    create_vm_instance,
    delete_batch_task,
    delete_config_record,
    delete_container,
    delete_domain,
    delete_file_dist_task,
    delete_hardware,
    delete_host,
    delete_script_category,
    delete_script_repository,
    delete_vm_instance,
    export_hosts_xlsx,
    get_batch_task_detail,
    get_config_model,
    get_file_dist_task_detail,
    get_host_form_options,
    get_hosts_with_first_ip,
    get_script_category_tree,
    get_web_terminal_context,
    list_batch_tasks,
    list_config_records,
    list_containers,
    list_domains,
    list_file_dist_tasks,
    list_hardware,
    list_hosts,
    list_script_categories,
    list_script_repositories,
    list_vm_instances,
    list_web_terminal_hosts,
    update_config_record,
    update_container,
    update_domain,
    update_hardware,
    update_host,
    update_script_category,
    update_script_repository,
    update_vm_instance,
)


def _json_service(handler: Callable[..., dict[str, Any]], *args: Any, **kwargs: Any) -> JsonResponse:
    try:
        return JsonResponse(handler(*args, **kwargs))
    except ServiceError as exc:
        return JsonResponse({"code": 1, "msg": exc.msg}, status=exc.http_status)


def _json_from_body(handler: Callable[[dict[str, Any]], dict[str, Any]], request) -> JsonResponse:
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"code": 1, "msg": "无效的请求数据"}, status=400)
    return _json_service(handler, body)


def _page_params(request) -> tuple[int, int, str]:
    return (
        int(request.GET.get("page", 1)),
        int(request.GET.get("limit", 20)),
        request.GET.get("keyword", "").strip(),
    )


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


# ===== 脚本分类 =====

@login_required
def script_category_list_view(request):
    context = {"categories_tree": json.dumps(get_script_category_tree())}
    return render(request, "common/script_category_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def script_category_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(list_script_categories, page=page, limit=limit, keyword=keyword)
    if request.method == "POST":
        return _json_from_body(create_script_category, request)
    if request.method == "PUT":
        return _json_from_body(update_script_category, request)
    return _json_from_body(lambda body: delete_script_category(body.get("id")), request)


# ===== 脚本仓库 =====

@login_required
def script_repository_list_view(request):
    context = {
        "script_types": json.dumps([{"value": k, "label": v} for k, v in SCRIPT_TYPE_LABELS.items()]),
        "categories_tree": json.dumps(get_script_category_tree()),
    }
    return render(request, "common/script_repository_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def script_repository_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(
            list_script_repositories,
            page=page,
            limit=limit,
            keyword=keyword,
            category_id=request.GET.get("category_id", "").strip(),
            script_type=request.GET.get("script_type", "").strip(),
        )
    if request.method == "POST":
        return _json_from_body(create_script_repository, request)
    if request.method == "PUT":
        return _json_from_body(update_script_repository, request)
    return _json_from_body(lambda body: delete_script_repository(body.get("id")), request)


# ===== 批量执行 =====

@login_required
def batch_execute_list_view(request):
    context = {
        "hosts": json.dumps(get_hosts_with_first_ip()),
        "scripts": json.dumps(list(ScriptRepository.objects.filter(status="enabled").values("id", "name", "script_type"))),
    }
    return render(request, "common/batch_execute_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def batch_execute_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(list_batch_tasks, page=page, limit=limit, keyword=keyword)
    if request.method == "POST":
        return _json_from_body(create_batch_task, request)
    return _json_from_body(lambda body: delete_batch_task(body.get("id")), request)


@login_required
def batch_execute_detail_api(request, task_id):
    return _json_service(get_batch_task_detail, task_id)


# ===== 文件分发 =====

@login_required
def file_dist_list_view(request):
    context = {"hosts": json.dumps(get_hosts_with_first_ip())}
    return render(request, "common/file_dist_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "DELETE"])
def file_dist_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(list_file_dist_tasks, page=page, limit=limit, keyword=keyword)
    if request.method == "POST":
        form_data = {
            "name": request.POST.get("name", ""),
            "source_type": request.POST.get("source_type", "local"),
            "host_ids": request.POST.get("host_ids", ""),
            "dest_path": request.POST.get("dest_path", ""),
            "dest_owner": request.POST.get("dest_owner", "root"),
            "dest_group": request.POST.get("dest_group", "root"),
            "dest_mode": request.POST.get("dest_mode", "0644"),
            "backup": request.POST.get("backup") == "1",
            "creator": request.POST.get("creator", ""),
            "remark": request.POST.get("remark", ""),
            "local_file": request.FILES.get("local_file"),
            "source_host_id": request.POST.get("source_host_id", ""),
            "source_path": request.POST.get("source_path", ""),
        }
        return _json_service(create_file_dist_task, form_data)
    return _json_from_body(lambda body: delete_file_dist_task(body.get("id")), request)


@login_required
def file_dist_detail_api(request, task_id):
    return _json_service(get_file_dist_task_detail, task_id)


# ===== 通用配置 =====

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
    try:
        model_class, has_choices = get_config_model(model_key)
    except ServiceError as exc:
        return JsonResponse({"code": 1, "msg": exc.msg}, status=exc.http_status)

    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(
            list_config_records,
            model_class,
            page=page,
            limit=limit,
            keyword=keyword,
            has_choices=has_choices,
        )
    if request.method == "POST":
        return _json_from_body(
            lambda body: create_config_record(model_class, body, has_choices=has_choices),
            request,
        )
    if request.method == "PUT":
        return _json_from_body(
            lambda body: update_config_record(model_class, body, has_choices=has_choices),
            request,
        )
    return _json_from_body(
        lambda body: delete_config_record(model_class, body.get("id")),
        request,
    )


# ===== 主机维护 =====

@login_required
def host_list_view(request):
    return render(request, "common/host_list.html", get_host_form_options())


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def host_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(list_hosts, page=page, limit=limit, keyword=keyword)
    if request.method == "POST":
        return _json_from_body(create_host, request)
    if request.method == "PUT":
        return _json_from_body(update_host, request)
    return _json_from_body(lambda body: delete_host(body.get("id")), request)


@login_required
def host_export_view(request):
    content = export_hosts_xlsx(request.GET.get("keyword", "").strip())
    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="host_list.xlsx"'
    return response


# ===== 主机硬件 =====

@login_required
def hardware_list_view(request):
    context = {"hosts": list(Host.objects.values("id", "display_name", "hostname"))}
    return render(request, "common/hardware_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def hardware_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(list_hardware, page=page, limit=limit, keyword=keyword)
    if request.method == "POST":
        return _json_from_body(create_hardware, request)
    if request.method == "PUT":
        return _json_from_body(update_hardware, request)
    return _json_from_body(lambda body: delete_hardware(body.get("id")), request)


# ===== 主机域名 =====

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
        page, limit, keyword = _page_params(request)
        return _json_service(list_domains, page=page, limit=limit, keyword=keyword)
    if request.method == "POST":
        return _json_from_body(create_domain, request)
    if request.method == "PUT":
        return _json_from_body(update_domain, request)
    return _json_from_body(lambda body: delete_domain(body.get("id")), request)


# ===== 虚拟机实例 =====

@login_required
def vm_list_view(request):
    context = {"hosts": list(Host.objects.values("id", "display_name", "hostname"))}
    return render(request, "common/vm_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def vm_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(list_vm_instances, page=page, limit=limit, keyword=keyword)
    if request.method == "POST":
        return _json_from_body(create_vm_instance, request)
    if request.method == "PUT":
        return _json_from_body(update_vm_instance, request)
    return _json_from_body(lambda body: delete_vm_instance(body.get("id")), request)


# ===== Docker 容器 =====

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
        page, limit, keyword = _page_params(request)
        return _json_service(list_containers, page=page, limit=limit, keyword=keyword)
    if request.method == "POST":
        return _json_from_body(create_container, request)
    if request.method == "PUT":
        return _json_from_body(update_container, request)
    return _json_from_body(lambda body: delete_container(body.get("id")), request)


# ===== 网页终端 =====

@login_required
def web_terminal_list_view(request):
    return render(request, "common/web_terminal_list.html")


@login_required
def web_terminal_page_view(request, host_id):
    context = get_web_terminal_context(host_id)
    if not context:
        return redirect("web_terminal_list")
    return render(request, "common/web_terminal.html", context)


@login_required
def web_terminal_hosts_api(request):
    return _json_service(list_web_terminal_hosts)
