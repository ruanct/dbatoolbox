import json
from typing import Any, Callable

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from .models import DatabaseInstance
from .services import (
    ServiceError,
    create_account,
    create_deploy_host,
    create_instance,
    create_replication_cluster,
    delete_account,
    delete_deploy_host,
    delete_instance,
    delete_replication_cluster,
    get_account_form_options,
    get_deploy_host_form_options,
    get_instance_form_options,
    get_replication_cluster_form_options,
    list_accounts,
    list_deploy_hosts,
    list_instances,
    list_replication_clusters,
    update_account,
    update_deploy_host,
    update_instance,
    update_replication_cluster,
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


def _instance_filter(request) -> int | None:
    raw = request.GET.get("instance_id", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _engine_filter(request) -> str | None:
    raw = request.GET.get("engine", "").strip()
    if not raw:
        return None
    valid = {value for value, _ in DatabaseInstance.ENGINE_CHOICES}
    return raw if raw in valid else None


def _derive_filter_engine(instance_id: str) -> str:
    if not instance_id:
        return ""
    try:
        iid = int(instance_id)
    except ValueError:
        return ""
    engine = DatabaseInstance.objects.filter(id=iid).values_list("engine", flat=True).first()
    return engine or ""


def _list_filter_params(request) -> dict[str, Any]:
    return {
        "engine": _engine_filter(request),
        "instance_id": _instance_filter(request),
    }


# ===== 复制集集群 =====

@login_required
def replication_cluster_list_view(request):
    options = get_replication_cluster_form_options()
    context = {key: json.dumps(val) for key, val in options.items()}
    return render(request, "dbmgr/replication_cluster_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def replication_cluster_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(
            list_replication_clusters,
            page=page,
            limit=limit,
            keyword=keyword,
            engine=_engine_filter(request),
        )
    if request.method == "POST":
        return _json_from_body(create_replication_cluster, request)
    if request.method == "PUT":
        return _json_from_body(update_replication_cluster, request)
    return _json_from_body(lambda body: delete_replication_cluster(body.get("id")), request)


# ===== DB 实例维护 =====

@login_required
def instance_list_view(request):
    options = get_instance_form_options()
    context = {key: json.dumps(val) for key, val in options.items()}
    return render(request, "dbmgr/instance_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def instance_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        return _json_service(
            list_instances,
            page=page,
            limit=limit,
            keyword=keyword,
            engine=_engine_filter(request),
        )
    if request.method == "POST":
        return _json_from_body(create_instance, request)
    if request.method == "PUT":
        return _json_from_body(update_instance, request)
    return _json_from_body(lambda body: delete_instance(body.get("id")), request)


# ===== 部署节点 =====

@login_required
def deploy_host_list_view(request):
    options = get_deploy_host_form_options()
    context = {key: json.dumps(val) for key, val in options.items()}
    context["filter_instance_id"] = request.GET.get("instance_id", "")
    context["filter_engine"] = _derive_filter_engine(context["filter_instance_id"])
    return render(request, "dbmgr/deploy_host_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def deploy_host_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        filters = _list_filter_params(request)
        return _json_service(
            list_deploy_hosts,
            page=page,
            limit=limit,
            keyword=keyword,
            instance_id=filters["instance_id"],
            engine=filters["engine"],
        )
    if request.method == "POST":
        return _json_from_body(create_deploy_host, request)
    if request.method == "PUT":
        return _json_from_body(update_deploy_host, request)
    return _json_from_body(lambda body: delete_deploy_host(body.get("id")), request)


# ===== 连接账号 =====

@login_required
def account_list_view(request):
    options = get_account_form_options()
    context = {key: json.dumps(val) for key, val in options.items()}
    context["filter_instance_id"] = request.GET.get("instance_id", "")
    context["filter_engine"] = _derive_filter_engine(context["filter_instance_id"])
    return render(request, "dbmgr/account_list.html", context)


@login_required
@require_http_methods(["GET", "POST", "PUT", "DELETE"])
def account_api_view(request):
    if request.method == "GET":
        page, limit, keyword = _page_params(request)
        filters = _list_filter_params(request)
        return _json_service(
            list_accounts,
            page=page,
            limit=limit,
            keyword=keyword,
            instance_id=filters["instance_id"],
            engine=filters["engine"],
        )
    if request.method == "POST":
        return _json_from_body(create_account, request)
    if request.method == "PUT":
        return _json_from_body(update_account, request)
    return _json_from_body(lambda body: delete_account(body.get("id")), request)
