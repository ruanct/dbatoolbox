"""apps.dbmgr 核心业务逻辑层。"""
from __future__ import annotations

import re
from typing import Any

from django.core.paginator import Paginator
from django.db.models import Q

from .models import (
    DatabaseAccount,
    DatabaseInstance,
    DatabaseInstanceHost,
    DatabaseReplicationCluster,
)

DEFAULT_PORTS: dict[str, int] = {
    "mysql": 3306,
    "postgresql": 5432,
    "oracle": 1521,
    "mssql": 1433,
}

ENGINE_LABELS = dict(DatabaseInstance.ENGINE_CHOICES)
TOPOLOGY_LABELS = dict(DatabaseInstance.TOPOLOGY_CHOICES)
CLUSTER_STYLE_LABELS = dict(DatabaseInstance.CLUSTER_STYLE_CHOICES)
ROLE_LABELS = dict(DatabaseInstance.ROLE_CHOICES)
STATUS_LABELS = dict(DatabaseInstance.STATUS_CHOICES)
REPLICATION_TYPE_LABELS = dict(DatabaseReplicationCluster.REPLICATION_TYPE_CHOICES)


class ServiceError(Exception):
    """业务异常，由视图层转换为 HTTP 响应。"""

    def __init__(self, msg: str, http_status: int = 400) -> None:
        super().__init__(msg)
        self.msg = msg
        self.http_status = http_status


def _choice_label(choices_map: dict[str, str], value: str) -> str:
    return choices_map.get(value, value)


def get_instance_form_options() -> dict[str, Any]:
    from apps.common.models import Business, Environment

    clusters = DatabaseReplicationCluster.objects.order_by("name").values("id", "name", "engine")
    return {
        "environments": list(Environment.objects.values("id", "name", "code")),
        "businesses": list(Business.objects.values("id", "name")),
        "replication_clusters": list(clusters),
        "engines": [{"value": v, "label": l} for v, l in DatabaseInstance.ENGINE_CHOICES],
        "topologies": [{"value": v, "label": l} for v, l in DatabaseInstance.TOPOLOGY_CHOICES],
        "cluster_styles": [{"value": v, "label": l} for v, l in DatabaseInstance.CLUSTER_STYLE_CHOICES],
        "roles": [{"value": v, "label": l} for v, l in DatabaseInstance.ROLE_CHOICES],
        "statuses": [{"value": v, "label": l} for v, l in DatabaseInstance.STATUS_CHOICES],
        "replication_types": [
            {"value": v, "label": l} for v, l in DatabaseReplicationCluster.REPLICATION_TYPE_CHOICES
        ],
    }


def get_replication_cluster_form_options() -> dict[str, Any]:
    instances = (
        DatabaseInstance.objects.filter(topology="replication")
        .select_related("replication_cluster")
        .order_by("instance_name")
        .values("id", "instance_name", "engine", "role", "replication_cluster_id")
    )
    return {
        "engines": [{"value": v, "label": l} for v, l in DatabaseInstance.ENGINE_CHOICES],
        "replication_types": [
            {"value": v, "label": l} for v, l in DatabaseReplicationCluster.REPLICATION_TYPE_CHOICES
        ],
        "instances": list(instances),
    }


def _validate_instance_topology(body: dict[str, Any], *, is_create: bool) -> None:
    topology = body.get("topology", "standalone")
    cluster_style = (body.get("cluster_style") or "").strip()
    replication_cluster_id = body.get("replication_cluster_id") or None

    engine = body.get("engine")
    if topology == "ha_cluster" and not cluster_style:
        if engine == "oracle":
            raise ServiceError("高可用集群必须选择集群类型（Oracle 请选择 Oracle RAC）")
        raise ServiceError("高可用集群必须选择集群类型")
    if topology == "replication":
        if not replication_cluster_id:
            raise ServiceError("复制集成员必须选择所属复制集")
    elif replication_cluster_id:
        raise ServiceError("仅复制集成员可关联复制集")

    if topology != "ha_cluster" and cluster_style:
        raise ServiceError("仅高可用集群可填写集群类型")

    if topology == "ha_cluster" and engine == "oracle" and cluster_style == "rac":
        if not (body.get("service_name") or "").strip() and not (body.get("sid") or "").strip():
            raise ServiceError("Oracle RAC 需填写 Service Name 或 SID")


def serialize_replication_cluster(obj: DatabaseReplicationCluster) -> dict[str, Any]:
    primary_name = ""
    if obj.primary_instance_id:
        primary_name = obj.primary_instance.instance_name
    instance_count = obj.instances.count()
    return {
        "id": obj.id,
        "name": obj.name,
        "engine": obj.engine,
        "engine__display": _choice_label(ENGINE_LABELS, obj.engine),
        "replication_type": obj.replication_type,
        "replication_type__display": _choice_label(REPLICATION_TYPE_LABELS, obj.replication_type),
        "primary_instance_id": obj.primary_instance_id,
        "primary_instance__display": primary_name,
        "instance_count": instance_count,
        "remark": obj.remark,
        "created_at": obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": obj.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def list_replication_clusters(
    *, page: int, limit: int, keyword: str, engine: str | None = None,
) -> dict[str, Any]:
    queryset = DatabaseReplicationCluster.objects.select_related("primary_instance").order_by("-id")
    if engine:
        queryset = queryset.filter(engine=engine)
    if keyword:
        queryset = queryset.filter(
            Q(name__icontains=keyword) | Q(remark__icontains=keyword),
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_replication_cluster(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_replication_cluster(body: dict[str, Any]) -> dict[str, Any]:
    name = (body.get("name") or "").strip()
    if not name:
        raise ServiceError("请填写复制集名称")
    engine = body.get("engine")
    replication_type = body.get("replication_type")
    if not engine:
        raise ServiceError("请选择数据库类型")
    if not replication_type:
        raise ServiceError("请选择复制类型")

    primary_instance_id = body.get("primary_instance_id") or None
    if primary_instance_id:
        try:
            inst = DatabaseInstance.objects.get(id=primary_instance_id)
        except DatabaseInstance.DoesNotExist as exc:
            raise ServiceError("主实例不存在") from exc
        if inst.engine != engine:
            raise ServiceError("主实例数据库类型与复制集不一致")

    obj = DatabaseReplicationCluster.objects.create(
        name=name,
        engine=engine,
        replication_type=replication_type,
        primary_instance_id=primary_instance_id,
        remark=(body.get("remark") or "").strip(),
    )
    if primary_instance_id and not DatabaseInstance.objects.filter(
        id=primary_instance_id, replication_cluster_id=obj.id,
    ).exists():
        DatabaseInstance.objects.filter(id=primary_instance_id).update(replication_cluster_id=obj.id)

    return {"code": 0, "msg": "添加成功", "id": obj.id}


def update_replication_cluster(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DatabaseReplicationCluster.objects.get(id=obj_id)
    except DatabaseReplicationCluster.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc

    name = (body.get("name") or "").strip()
    if not name:
        raise ServiceError("请填写复制集名称")
    engine = body.get("engine", obj.engine)
    replication_type = body.get("replication_type", obj.replication_type)
    primary_instance_id = body.get("primary_instance_id") or None

    if primary_instance_id:
        try:
            inst = DatabaseInstance.objects.get(id=primary_instance_id)
        except DatabaseInstance.DoesNotExist as exc:
            raise ServiceError("主实例不存在") from exc
        if inst.engine != engine:
            raise ServiceError("主实例数据库类型与复制集不一致")

    obj.name = name
    obj.engine = engine
    obj.replication_type = replication_type
    obj.primary_instance_id = primary_instance_id
    obj.remark = (body.get("remark") or "").strip()
    obj.save()
    return {"code": 0, "msg": "更新成功"}


def delete_replication_cluster(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DatabaseReplicationCluster.objects.get(id=obj_id)
    except DatabaseReplicationCluster.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    if obj.instances.exists():
        raise ServiceError("复制集下仍有实例，请先解除或删除实例")
    obj.delete()
    return {"code": 0, "msg": "删除成功"}


def serialize_instance(obj: DatabaseInstance) -> dict[str, Any]:
    default_account = obj.accounts.filter(is_default=True).first()
    return {
        "id": obj.id,
        "instance_name": obj.instance_name,
        "engine": obj.engine,
        "engine__display": _choice_label(ENGINE_LABELS, obj.engine),
        "topology": obj.topology,
        "topology__display": _choice_label(TOPOLOGY_LABELS, obj.topology),
        "cluster_style": obj.cluster_style,
        "cluster_style__display": _choice_label(CLUSTER_STYLE_LABELS, obj.cluster_style) if obj.cluster_style else "",
        "role": obj.role,
        "role__display": _choice_label(ROLE_LABELS, obj.role),
        "status": obj.status,
        "status__display": _choice_label(STATUS_LABELS, obj.status),
        "version": obj.version,
        "environment_id": obj.environment_id,
        "environment__display": obj.environment.name,
        "business_id": obj.business_id,
        "business__display": obj.business.name,
        "replication_cluster_id": obj.replication_cluster_id,
        "replication_cluster__display": obj.replication_cluster.name if obj.replication_cluster_id else "",
        "connect_host": obj.connect_host,
        "port": obj.port,
        "read_connect_host": obj.read_connect_host,
        "read_port": obj.read_port,
        "db_name": obj.db_name,
        "charset": obj.charset,
        "sid": obj.sid,
        "service_name": obj.service_name,
        "is_ssl": obj.is_ssl,
        "remark": obj.remark,
        "default_account_name": (
            _full_account_name(
                default_account.account_name,
                default_account.grant_host,
                obj.engine,
            )
            if default_account
            else ""
        ),
        "deploy_host_count": obj.deploy_hosts.count(),
        "created_at": obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": obj.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def list_instances(
    *, page: int, limit: int, keyword: str, engine: str | None = None,
) -> dict[str, Any]:
    queryset = (
        DatabaseInstance.objects.select_related(
            "environment", "business", "replication_cluster",
        )
        .prefetch_related("accounts", "deploy_hosts")
        .order_by("-id")
    )
    if engine:
        queryset = queryset.filter(engine=engine)
    if keyword:
        queryset = queryset.filter(
            Q(instance_name__icontains=keyword)
            | Q(connect_host__icontains=keyword)
            | Q(db_name__icontains=keyword)
            | Q(remark__icontains=keyword),
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_instance(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def _parse_instance_body(body: dict[str, Any]) -> dict[str, Any]:
    engine = body.get("engine")
    port = body.get("port")
    if port in (None, ""):
        port = DEFAULT_PORTS.get(engine, 3306)
    else:
        port = int(port)

    return {
        "instance_name": (body.get("instance_name") or "").strip(),
        "engine": engine,
        "topology": body.get("topology", "standalone"),
        "cluster_style": (body.get("cluster_style") or "").strip(),
        "role": body.get("role", "master"),
        "status": body.get("status", "online"),
        "version": (body.get("version") or "").strip(),
        "environment_id": body.get("environment_id"),
        "business_id": body.get("business_id"),
        "replication_cluster_id": body.get("replication_cluster_id") or None,
        "connect_host": (body.get("connect_host") or "").strip(),
        "port": port,
        "read_connect_host": (body.get("read_connect_host") or "").strip(),
        "read_port": int(body["read_port"]) if body.get("read_port") not in (None, "") else None,
        "db_name": (body.get("db_name") or "").strip(),
        "charset": (body.get("charset") or "").strip(),
        "sid": (body.get("sid") or "").strip(),
        "service_name": (body.get("service_name") or "").strip(),
        "is_ssl": bool(body.get("is_ssl")),
        "remark": (body.get("remark") or "").strip(),
    }


def create_instance(body: dict[str, Any]) -> dict[str, Any]:
    fields = _parse_instance_body(body)
    if not fields["instance_name"]:
        raise ServiceError("请填写实例名称")
    if not fields["engine"]:
        raise ServiceError("请选择数据库类型")
    if not fields["environment_id"]:
        raise ServiceError("请选择所属环境")
    if not fields["business_id"]:
        raise ServiceError("请选择所属业务")
    if not fields["connect_host"]:
        raise ServiceError("请填写连接地址")

    _validate_instance_topology(fields, is_create=True)

    if fields["replication_cluster_id"]:
        try:
            cluster = DatabaseReplicationCluster.objects.get(id=fields["replication_cluster_id"])
        except DatabaseReplicationCluster.DoesNotExist as exc:
            raise ServiceError("复制集不存在") from exc
        if cluster.engine != fields["engine"]:
            raise ServiceError("实例数据库类型与复制集不一致")

    DatabaseInstance.objects.create(**fields)
    return {"code": 0, "msg": "添加成功"}


def update_instance(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DatabaseInstance.objects.get(id=obj_id)
    except DatabaseInstance.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc

    fields = _parse_instance_body(body)
    if not fields["instance_name"]:
        raise ServiceError("请填写实例名称")
    if not fields["environment_id"]:
        raise ServiceError("请选择所属环境")
    if not fields["business_id"]:
        raise ServiceError("请选择所属业务")
    if not fields["connect_host"]:
        raise ServiceError("请填写连接地址")

    if fields["engine"] != obj.engine:
        raise ServiceError("不允许修改数据库类型")

    _validate_instance_topology(fields, is_create=False)

    if fields["replication_cluster_id"]:
        try:
            cluster = DatabaseReplicationCluster.objects.get(id=fields["replication_cluster_id"])
        except DatabaseReplicationCluster.DoesNotExist as exc:
            raise ServiceError("复制集不存在") from exc
        if cluster.engine != fields["engine"]:
            raise ServiceError("实例数据库类型与复制集不一致")

    for key, value in fields.items():
        setattr(obj, key, value)
    obj.save()
    return {"code": 0, "msg": "更新成功"}


def delete_instance(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DatabaseInstance.objects.get(id=obj_id)
    except DatabaseInstance.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc

    DatabaseReplicationCluster.objects.filter(primary_instance_id=obj.id).update(primary_instance_id=None)
    obj.delete()
    return {"code": 0, "msg": "删除成功"}


# ==================== 部署节点 ====================

ACCOUNT_TYPE_LABELS = dict(DatabaseAccount.ACCOUNT_TYPE_CHOICES)
GRANT_HOST_PATTERN = re.compile(r"^[A-Za-z0-9_.%-]+$")


def _full_account_name(account_name: str, grant_host: str, engine: str) -> str:
    if engine == "mysql" and grant_host:
        return f"{account_name}@{grant_host}"
    return account_name


def _normalize_grant_host(instance: DatabaseInstance, grant_host: str) -> str:
    grant_host = (grant_host or "").strip()
    if instance.engine != "mysql":
        return ""
    if not grant_host:
        raise ServiceError("MySQL 账号必须填写授权主机")
    if len(grant_host) > 255:
        raise ServiceError("授权主机长度不能超过 255")
    if "," in grant_host:
        raise ServiceError("授权主机仅支持单个 Host 模式，多个网段请分别建账号")
    if not GRANT_HOST_PATTERN.match(grant_host):
        raise ServiceError("授权主机格式不正确")
    return grant_host


def get_simple_instance_options() -> list[dict[str, Any]]:
    return list(
        DatabaseInstance.objects.order_by("instance_name").values("id", "instance_name", "engine"),
    )


def get_deploy_host_form_options() -> dict[str, Any]:
    from apps.common.models import Host

    return {
        "instances": get_simple_instance_options(),
        "engines": [{"value": v, "label": l} for v, l in DatabaseInstance.ENGINE_CHOICES],
        "hosts": list(Host.objects.order_by("display_name").values("id", "display_name", "hostname")),
    }


def get_account_form_options() -> dict[str, Any]:
    return {
        "instances": get_simple_instance_options(),
        "engines": [{"value": v, "label": l} for v, l in DatabaseInstance.ENGINE_CHOICES],
        "account_types": [
            {"value": v, "label": l} for v, l in DatabaseAccount.ACCOUNT_TYPE_CHOICES
        ],
    }


def _ensure_single_primary_host(instance_id: int, current_id: int | None = None) -> None:
    DatabaseInstanceHost.objects.filter(instance_id=instance_id, is_primary=True).exclude(
        id=current_id or 0,
    ).update(is_primary=False)


def _ensure_single_default_account(instance_id: int, current_id: int | None = None) -> None:
    DatabaseAccount.objects.filter(instance_id=instance_id, is_default=True).exclude(
        id=current_id or 0,
    ).update(is_default=False)


def serialize_deploy_host(obj: DatabaseInstanceHost) -> dict[str, Any]:
    return {
        "id": obj.id,
        "instance_id": obj.instance_id,
        "instance__display": obj.instance.instance_name,
        "engine__display": _choice_label(ENGINE_LABELS, obj.instance.engine),
        "host_id": obj.host_id,
        "host__display": str(obj.host),
        "node_name": obj.node_name,
        "node_sid": obj.node_sid,
        "node_service_name": obj.node_service_name,
        "listener_host": obj.listener_host,
        "listener_port": obj.listener_port,
        "is_primary": obj.is_primary,
        "sort_order": obj.sort_order,
        "remark": obj.remark,
        "created_at": obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": obj.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def list_deploy_hosts(
    *,
    page: int,
    limit: int,
    keyword: str,
    instance_id: int | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    queryset = (
        DatabaseInstanceHost.objects.select_related("instance", "host")
        .order_by("instance_id", "sort_order", "id")
    )
    if instance_id:
        queryset = queryset.filter(instance_id=instance_id)
    if engine:
        queryset = queryset.filter(instance__engine=engine)
    if keyword:
        queryset = queryset.filter(
            Q(instance__instance_name__icontains=keyword)
            | Q(host__display_name__icontains=keyword)
            | Q(host__hostname__icontains=keyword)
            | Q(node_name__icontains=keyword)
            | Q(node_sid__icontains=keyword)
            | Q(listener_host__icontains=keyword)
            | Q(remark__icontains=keyword),
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_deploy_host(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_deploy_host(body: dict[str, Any]) -> dict[str, Any]:
    instance_id = body.get("instance_id")
    host_id = body.get("host_id")
    if not instance_id:
        raise ServiceError("请选择关联实例")
    if not host_id:
        raise ServiceError("请选择部署主机")
    if not DatabaseInstance.objects.filter(id=instance_id).exists():
        raise ServiceError("关联实例不存在")
    if DatabaseInstanceHost.objects.filter(instance_id=instance_id, host_id=host_id).exists():
        raise ServiceError("该实例已关联此主机")

    is_primary = bool(body.get("is_primary"))
    listener_port = body.get("listener_port")
    obj = DatabaseInstanceHost.objects.create(
        instance_id=instance_id,
        host_id=host_id,
        node_name=(body.get("node_name") or "").strip(),
        node_sid=(body.get("node_sid") or "").strip(),
        node_service_name=(body.get("node_service_name") or "").strip(),
        listener_host=(body.get("listener_host") or "").strip(),
        listener_port=int(listener_port) if listener_port not in (None, "") else None,
        is_primary=is_primary,
        sort_order=int(body.get("sort_order") or 0),
        remark=(body.get("remark") or "").strip(),
    )
    if is_primary:
        _ensure_single_primary_host(instance_id, obj.id)
    return {"code": 0, "msg": "添加成功"}


def update_deploy_host(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DatabaseInstanceHost.objects.get(id=obj_id)
    except DatabaseInstanceHost.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc

    instance_id = body.get("instance_id", obj.instance_id)
    host_id = body.get("host_id", obj.host_id)
    if not instance_id or not host_id:
        raise ServiceError("请选择关联实例和部署主机")
    if DatabaseInstanceHost.objects.filter(instance_id=instance_id, host_id=host_id).exclude(
        id=obj_id,
    ).exists():
        raise ServiceError("该实例已关联此主机")

    listener_port = body.get("listener_port")
    is_primary = bool(body.get("is_primary", obj.is_primary))

    obj.instance_id = instance_id
    obj.host_id = host_id
    obj.node_name = (body.get("node_name") or "").strip()
    obj.node_sid = (body.get("node_sid") or "").strip()
    obj.node_service_name = (body.get("node_service_name") or "").strip()
    obj.listener_host = (body.get("listener_host") or "").strip()
    obj.listener_port = int(listener_port) if listener_port not in (None, "") else None
    obj.is_primary = is_primary
    obj.sort_order = int(body.get("sort_order") if body.get("sort_order") is not None else obj.sort_order)
    obj.remark = (body.get("remark") or "").strip()
    obj.save()
    if is_primary:
        _ensure_single_primary_host(instance_id, obj.id)
    return {"code": 0, "msg": "更新成功"}


def delete_deploy_host(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        DatabaseInstanceHost.objects.get(id=obj_id).delete()
    except DatabaseInstanceHost.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {"code": 0, "msg": "删除成功"}


# ==================== 连接账号 ====================

def serialize_account(obj: DatabaseAccount) -> dict[str, Any]:
    engine = obj.instance.engine
    grant_host = obj.grant_host if engine == "mysql" else ""
    return {
        "id": obj.id,
        "instance_id": obj.instance_id,
        "instance__display": obj.instance.instance_name,
        "engine": engine,
        "type_display": _choice_label(ENGINE_LABELS, engine),
        "account_type": obj.account_type,
        "account_type__display": _choice_label(ACCOUNT_TYPE_LABELS, obj.account_type),
        "account_name": obj.account_name,
        "grant_host": grant_host,
        "full_account_name": _full_account_name(obj.account_name, grant_host, engine),
        "account_pswd": obj.account_pswd,
        "default_schema": obj.default_schema,
        "is_default": obj.is_default,
        "remark": obj.remark,
        "created_at": obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": obj.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def list_accounts(
    *,
    page: int,
    limit: int,
    keyword: str,
    instance_id: int | None = None,
    engine: str | None = None,
) -> dict[str, Any]:
    queryset = DatabaseAccount.objects.select_related("instance").order_by("instance_id", "-is_default", "id")
    if instance_id:
        queryset = queryset.filter(instance_id=instance_id)
    if engine:
        queryset = queryset.filter(instance__engine=engine)
    if keyword:
        queryset = queryset.filter(
            Q(instance__instance_name__icontains=keyword)
            | Q(account_name__icontains=keyword)
            | Q(grant_host__icontains=keyword)
            | Q(default_schema__icontains=keyword)
            | Q(remark__icontains=keyword),
        )
    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [serialize_account(item) for item in page_obj.object_list]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


def create_account(body: dict[str, Any]) -> dict[str, Any]:
    instance_id = body.get("instance_id")
    account_name = (body.get("account_name") or "").strip()
    account_pswd = body.get("account_pswd") or ""
    if not instance_id:
        raise ServiceError("请选择关联实例")
    if not account_name:
        raise ServiceError("请填写账号名称")
    if not account_pswd:
        raise ServiceError("请填写账号密码")
    try:
        instance = DatabaseInstance.objects.get(id=instance_id)
    except DatabaseInstance.DoesNotExist as exc:
        raise ServiceError("关联实例不存在") from exc

    grant_host = _normalize_grant_host(instance, body.get("grant_host", ""))
    identity_label = _full_account_name(account_name, grant_host, instance.engine)
    if DatabaseAccount.objects.filter(
        instance_id=instance_id,
        account_name=account_name,
        grant_host=grant_host,
    ).exists():
        raise ServiceError(f"该实例下账号 {identity_label} 已存在")

    is_default = bool(body.get("is_default"))
    obj = DatabaseAccount.objects.create(
        instance_id=instance_id,
        account_type=body.get("account_type", "admin"),
        account_name=account_name,
        grant_host=grant_host,
        account_pswd=account_pswd,
        default_schema=(body.get("default_schema") or "").strip(),
        is_default=is_default,
        remark=(body.get("remark") or "").strip(),
    )
    if is_default:
        _ensure_single_default_account(instance_id, obj.id)
    elif not DatabaseAccount.objects.filter(instance_id=instance_id, is_default=True).exists():
        obj.is_default = True
        obj.save(update_fields=["is_default"])
    return {"code": 0, "msg": "添加成功"}


def update_account(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DatabaseAccount.objects.get(id=obj_id)
    except DatabaseAccount.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc

    instance_id = body.get("instance_id", obj.instance_id)
    account_name = (body.get("account_name") or "").strip()
    if not instance_id:
        raise ServiceError("请选择关联实例")
    if not account_name:
        raise ServiceError("请填写账号名称")
    try:
        instance = DatabaseInstance.objects.get(id=instance_id)
    except DatabaseInstance.DoesNotExist as exc:
        raise ServiceError("关联实例不存在") from exc

    grant_host = _normalize_grant_host(instance, body.get("grant_host", obj.grant_host))
    identity_label = _full_account_name(account_name, grant_host, instance.engine)
    if DatabaseAccount.objects.filter(
        instance_id=instance_id,
        account_name=account_name,
        grant_host=grant_host,
    ).exclude(id=obj_id).exists():
        raise ServiceError(f"该实例下账号 {identity_label} 已存在")

    account_pswd = body.get("account_pswd")
    is_default = bool(body.get("is_default", obj.is_default))

    obj.instance_id = instance_id
    obj.account_type = body.get("account_type", obj.account_type)
    obj.account_name = account_name
    obj.grant_host = grant_host
    if account_pswd:
        obj.account_pswd = account_pswd
    obj.default_schema = (body.get("default_schema") or "").strip()
    obj.is_default = is_default
    obj.remark = (body.get("remark") or "").strip()
    obj.save()
    if is_default:
        _ensure_single_default_account(instance_id, obj.id)
    return {"code": 0, "msg": "更新成功"}


def delete_account(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DatabaseAccount.objects.get(id=obj_id)
    except DatabaseAccount.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    instance_id = obj.instance_id
    was_default = obj.is_default
    obj.delete()
    if was_default:
        first = DatabaseAccount.objects.filter(instance_id=instance_id).order_by("id").first()
        if first:
            first.is_default = True
            first.save(update_fields=["is_default"])
    return {"code": 0, "msg": "删除成功"}


# ==================== 监控大屏 ====================

DASHBOARD_ENGINE_ORDER = [value for value, _ in DatabaseInstance.ENGINE_CHOICES]
DASHBOARD_TOPOLOGY_ORDER = ["ha_cluster", "replication", "standalone"]
DASHBOARD_TOPOLOGY_LABELS = {
    "ha_cluster": "高可用集群",
    "replication": "复制集",
    "standalone": "单实例",
}


def _serialize_dashboard_instance(
    obj: DatabaseInstance,
    *,
    host_ip_map: dict[int, str] | None = None,
) -> dict[str, Any]:
    from .probe_services import PROBE_STATUS_LABELS, resolve_deploy_host_endpoint

    data = {
        "id": obj.id,
        "instance_name": obj.instance_name,
        "topology": obj.topology,
        "connect_host": obj.connect_host,
        "port": obj.port,
        "probe_status": obj.probe_status,
        "probe_status_label": _choice_label(PROBE_STATUS_LABELS, obj.probe_status),
        "probe_message": obj.probe_message,
        "latency_ms": obj.latency_ms,
        "last_probed_at": obj.last_probed_at.strftime("%Y-%m-%d %H:%M:%S") if obj.last_probed_at else "",
        "role": obj.role,
        "role_label": _choice_label(ROLE_LABELS, obj.role),
        "cluster_style": obj.cluster_style,
        "cluster_style_label": _choice_label(CLUSTER_STYLE_LABELS, obj.cluster_style) if obj.cluster_style else "",
        "environment": obj.environment.name,
        "business": obj.business.name,
        "replication_cluster_id": obj.replication_cluster_id,
        "replication_cluster_name": obj.replication_cluster.name if obj.replication_cluster_id else "",
        "deploy_hosts": [],
    }
    if obj.topology == "ha_cluster":
        for deploy_host in obj.deploy_hosts.all():
            connect_host, port = resolve_deploy_host_endpoint(deploy_host, host_ip_map=host_ip_map)
            data["deploy_hosts"].append({
                "id": deploy_host.id,
                "node_name": deploy_host.node_name or str(deploy_host.host),
                "host_display": str(deploy_host.host),
                "connect_host": connect_host,
                "port": port,
                "node_sid": deploy_host.node_sid,
                "node_service_name": deploy_host.node_service_name,
                "is_primary": deploy_host.is_primary,
                "probe_status": deploy_host.probe_status,
                "probe_status_label": _choice_label(PROBE_STATUS_LABELS, deploy_host.probe_status),
                "probe_message": deploy_host.probe_message,
                "latency_ms": deploy_host.latency_ms,
                "last_probed_at": (
                    deploy_host.last_probed_at.strftime("%Y-%m-%d %H:%M:%S")
                    if deploy_host.last_probed_at
                    else ""
                ),
            })
    return data


def _accumulate_probe_status(summary: dict[str, int], status: str) -> None:
    summary["total"] += 1
    if status in summary:
        summary[status] += 1
    else:
        summary["unknown"] += 1


def _count_dashboard_summary(instances: list[DatabaseInstance]) -> dict[str, int]:
    summary = {"total": 0, "alive": 0, "dead": 0, "maintenance": 0, "unknown": 0}
    for instance in instances:
        _accumulate_probe_status(summary, instance.probe_status)
        if instance.topology == "ha_cluster":
            for deploy_host in instance.deploy_hosts.all():
                _accumulate_probe_status(summary, deploy_host.probe_status)
    return summary


def _build_topology_sections(
    instances: list[DatabaseInstance],
    *,
    host_ip_map: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[DatabaseInstance]] = {key: [] for key in DASHBOARD_TOPOLOGY_ORDER}
    for instance in instances:
        grouped.setdefault(instance.topology, []).append(instance)

    sections: list[dict[str, Any]] = []
    for topology in DASHBOARD_TOPOLOGY_ORDER:
        items = grouped.get(topology, [])
        section: dict[str, Any] = {
            "topology": topology,
            "topology_label": DASHBOARD_TOPOLOGY_LABELS[topology],
            "summary": _count_dashboard_summary(items),
            "instances": [],
            "clusters": [],
        }
        if topology == "replication":
            cluster_map: dict[int, dict[str, Any]] = {}
            ungrouped: list[DatabaseInstance] = []
            for instance in items:
                if instance.replication_cluster_id:
                    bucket = cluster_map.setdefault(
                        instance.replication_cluster_id,
                        {
                            "cluster_id": instance.replication_cluster_id,
                            "cluster_name": instance.replication_cluster.name,
                            "instances": [],
                        },
                    )
                    bucket["instances"].append(
                        _serialize_dashboard_instance(instance, host_ip_map=host_ip_map),
                    )
                else:
                    ungrouped.append(instance)
            section["clusters"] = list(cluster_map.values())
            section["instances"] = [
                _serialize_dashboard_instance(item, host_ip_map=host_ip_map) for item in ungrouped
            ]
        else:
            section["instances"] = [
                _serialize_dashboard_instance(item, host_ip_map=host_ip_map) for item in items
            ]
        sections.append(section)
    return sections


def build_dashboard_data() -> dict[str, Any]:
    from django.db.models import Prefetch
    from django.utils import timezone

    from .probe_services import _build_host_ip_map

    queryset = (
        DatabaseInstance.objects.select_related(
            "environment", "business", "replication_cluster",
        )
        .prefetch_related(
            Prefetch(
                "deploy_hosts",
                queryset=DatabaseInstanceHost.objects.select_related("host").order_by(
                    "sort_order", "id",
                ),
            ),
        )
        .order_by("engine", "topology", "instance_name")
    )
    instance_list = list(queryset)
    host_ids = {
        deploy_host.host_id
        for instance in instance_list
        for deploy_host in instance.deploy_hosts.all()
    }
    host_ip_map = _build_host_ip_map(host_ids)

    engine_map: dict[str, list[DatabaseInstance]] = {key: [] for key in DASHBOARD_ENGINE_ORDER}
    for instance in instance_list:
        engine_map.setdefault(instance.engine, []).append(instance)

    engines: list[dict[str, Any]] = []
    overall = {"total": 0, "alive": 0, "dead": 0, "maintenance": 0, "unknown": 0}
    for engine in DASHBOARD_ENGINE_ORDER:
        items = engine_map.get(engine, [])
        summary = _count_dashboard_summary(items)
        for key in overall:
            overall[key] += summary.get(key, 0)
        engines.append({
            "engine": engine,
            "engine_label": _choice_label(ENGINE_LABELS, engine),
            "summary": summary,
            "topologies": _build_topology_sections(items, host_ip_map=host_ip_map),
        })

    latest_instance_probe = (
        DatabaseInstance.objects.exclude(last_probed_at__isnull=True)
        .order_by("-last_probed_at")
        .values_list("last_probed_at", flat=True)
        .first()
    )
    latest_host_probe = (
        DatabaseInstanceHost.objects.exclude(last_probed_at__isnull=True)
        .order_by("-last_probed_at")
        .values_list("last_probed_at", flat=True)
        .first()
    )
    latest_probe = latest_instance_probe
    if latest_host_probe and (not latest_probe or latest_host_probe > latest_probe):
        latest_probe = latest_host_probe
    return {
        "code": 0,
        "msg": "",
        "updated_at": latest_probe.strftime("%Y-%m-%d %H:%M:%S") if latest_probe else "",
        "summary": overall,
        "engines": engines,
        "server_time": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
