"""MySQL 部署参数模板 CRUD。"""
from __future__ import annotations

import re
from typing import Any

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q

from .deploy_constants import (
    MYSQL_PARAM_TEMPLATE_MAJOR_CHOICES,
    MYSQL_PARAM_TEMPLATE_RESERVED_NAMES,
    MYSQL_PARAM_TEMPLATE_STATUS_CHOICES,
)
from .models import DbDeployMysqlParamTemplate, DbDeployMysqlParamTemplateItem
from .services import ServiceError

_PARAM_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_TEMPLATE_CODE_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

STATUS_LABELS = dict(MYSQL_PARAM_TEMPLATE_STATUS_CHOICES)
MAJOR_LABELS = dict(MYSQL_PARAM_TEMPLATE_MAJOR_CHOICES)
SECTION_LABELS = dict(DbDeployMysqlParamTemplateItem.SECTION_CHOICES)


PARAM_NAME_TO_CONFIG_KEY: dict[str, str] = {
    "character-set-server": "character_set",
    "character_set_server": "character_set",
    "collation-server": "collation",
    "collation_server": "collation",
    "max_connections": "max_connections",
    "innodb_buffer_pool_size": "innodb_buffer_pool_size",
    "binlog_format": "binlog_format",
    "default_authentication_plugin": "default_authentication_plugin",
    "sql_mode": "sql_mode",
    "default-character-set": "client_character_set",
    "default_character_set": "client_character_set",
}


def _normalize_cnf_param_name(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")


def _config_key_for_param_name(param_name: str) -> str | None:
    key = PARAM_NAME_TO_CONFIG_KEY.get(param_name.strip())
    if key:
        return key
    return PARAM_NAME_TO_CONFIG_KEY.get(_normalize_cnf_param_name(param_name))


def list_mysql_param_template_options(
    *,
    major_version: str | None = None,
) -> list[dict[str, Any]]:
    queryset = DbDeployMysqlParamTemplate.objects.filter(status="enabled").order_by(
        "major_version", "-is_default", "title", "id",
    )
    if major_version:
        queryset = queryset.filter(major_version=major_version)
    return [
        {
            "template_code": item.template_code,
            "title": item.title,
            "major_version": item.major_version,
            "is_default": item.is_default,
        }
        for item in queryset
    ]


def load_enabled_mysql_param_template(template_code: str) -> DbDeployMysqlParamTemplate:
    code = (template_code or "").strip()
    if not code:
        raise ServiceError("请选择 MySQL 参数模板")
    try:
        obj = DbDeployMysqlParamTemplate.objects.prefetch_related("items").get(
            template_code=code,
            status="enabled",
        )
    except DbDeployMysqlParamTemplate.DoesNotExist as exc:
        raise ServiceError(f"未找到启用的 MySQL 参数模板: {code}") from exc
    return obj


def load_enabled_mysql_param_template_by_title(
    title: str,
    major_version: str,
) -> DbDeployMysqlParamTemplate:
    title = (title or "").strip()
    major_version = _validate_major_version(major_version)
    if not title:
        raise ServiceError("请选择 MySQL 参数模板")
    queryset = DbDeployMysqlParamTemplate.objects.prefetch_related("items").filter(
        title=title,
        major_version=major_version,
        status="enabled",
    )
    count = queryset.count()
    if count == 0:
        raise ServiceError(
            f"未找到启用的 MySQL 参数模板: major={major_version}, title={title}"
        )
    if count > 1:
        raise ServiceError(
            f"参数模板标题「{title}」在 MySQL {major_version} 下不唯一，请联系 DBA 处理"
        )
    return queryset.first()


def _apply_template_object_to_merged(
    merged: dict[str, Any],
    template: DbDeployMysqlParamTemplate,
    *,
    profile_major: str,
) -> None:
    profile_major = (profile_major or "").strip()
    if template.major_version != profile_major:
        raise ServiceError(
            f"参数模板 major={template.major_version} 与版本档案 major={profile_major} 不一致"
        )

    meta = merged.setdefault("meta", {})
    meta["mysql_param_template_code"] = template.template_code
    meta["mysql_param_template_title"] = template.title

    config = merged.setdefault("config", {})
    cnf_items: dict[str, list[dict[str, str]]] = {"mysqld": [], "client": []}

    for item in template.items.order_by("sort_order", "id"):
        param_value = (item.param_value or item.default_value or "").strip()
        if not param_value:
            continue

        config_key = _config_key_for_param_name(item.param_name)
        if config_key and item.section == "mysqld":
            config[config_key] = param_value

        section = item.section if item.section in cnf_items else "mysqld"
        cnf_items[section].append({
            "param_name": item.param_name.strip(),
            "param_value": param_value,
        })

    config["cnf_template_items"] = cnf_items


def apply_mysql_param_template_to_merged(
    merged: dict[str, Any],
    *,
    profile_major: str,
    template_code: str = "",
    template_title: str = "",
) -> None:
    """将参数模板合并进 resolved_params（在 user_params 覆盖之前调用）。"""
    code = (template_code or "").strip()
    title = (template_title or "").strip()
    if not code and not title:
        return

    if code:
        template = load_enabled_mysql_param_template(code)
    else:
        template = load_enabled_mysql_param_template_by_title(title, profile_major)

    _apply_template_object_to_merged(merged, template, profile_major=profile_major)


def get_mysql_param_template_title_options(major_version: str) -> dict[str, Any]:
    major_version = _validate_major_version(major_version)
    return {
        "code": 0,
        "msg": "",
        "data": list_mysql_param_template_options(major_version=major_version),
    }


def get_mysql_param_template_by_code(template_code: str) -> dict[str, Any]:
    obj = load_enabled_mysql_param_template(template_code)
    return {
        "code": 0,
        "msg": "",
        "data": _serialize_template(obj, include_items=True),
    }


def get_mysql_param_template_by_title(title: str, major_version: str) -> dict[str, Any]:
    obj = load_enabled_mysql_param_template_by_title(title, major_version)
    return {
        "code": 0,
        "msg": "",
        "data": _serialize_template(obj, include_items=True),
    }


def get_mysql_param_template_form_options() -> dict[str, Any]:
    return {
        "major_versions": [
            {"value": value, "label": label}
            for value, label in MYSQL_PARAM_TEMPLATE_MAJOR_CHOICES
        ],
        "statuses": [
            {"value": value, "label": label}
            for value, label in MYSQL_PARAM_TEMPLATE_STATUS_CHOICES
        ],
        "sections": [
            {"value": value, "label": label}
            for value, label in DbDeployMysqlParamTemplateItem.SECTION_CHOICES
        ],
    }


def _serialize_item(obj: DbDeployMysqlParamTemplateItem) -> dict[str, Any]:
    return {
        "id": obj.id,
        "sort_order": obj.sort_order,
        "section": obj.section,
        "section__display": SECTION_LABELS.get(obj.section, obj.section),
        "param_name": obj.param_name,
        "param_value": obj.param_value,
        "default_value": obj.default_value,
        "remark": obj.remark,
    }


def _serialize_template(
    obj: DbDeployMysqlParamTemplate,
    *,
    include_items: bool = False,
    item_count: int | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": obj.id,
        "template_code": obj.template_code,
        "title": obj.title,
        "major_version": obj.major_version,
        "major_version__display": MAJOR_LABELS.get(obj.major_version, obj.major_version),
        "status": obj.status,
        "status__display": STATUS_LABELS.get(obj.status, obj.status),
        "is_default": obj.is_default,
        "remark": obj.remark,
        "item_count": item_count if item_count is not None else obj.items.count(),
        "created_at": obj.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": obj.updated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if include_items:
        items = obj.items.order_by("sort_order", "id")
        data["items"] = [_serialize_item(item) for item in items]
    return data


def _validate_template_code(code: str) -> str:
    code = (code or "").strip()
    if not code:
        raise ServiceError("请填写模板编码")
    if len(code) > 64:
        raise ServiceError("模板编码长度不能超过 64")
    if not _TEMPLATE_CODE_RE.match(code):
        raise ServiceError("模板编码仅允许字母、数字、下划线、连字符")
    return code


def _validate_major_version(major: str) -> str:
    major = (major or "").strip()
    valid = {value for value, _ in MYSQL_PARAM_TEMPLATE_MAJOR_CHOICES}
    if major not in valid:
        raise ServiceError("请选择有效的 MySQL major 版本")
    return major


def _validate_status(status: str) -> str:
    status = (status or "enabled").strip()
    valid = {value for value, _ in MYSQL_PARAM_TEMPLATE_STATUS_CHOICES}
    if status not in valid:
        raise ServiceError("状态无效")
    return status


def _normalize_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        raise ServiceError("参数明细格式无效")
    if not raw_items:
        raise ServiceError("请至少添加一条参数明细")

    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    valid_sections = {value for value, _ in DbDeployMysqlParamTemplateItem.SECTION_CHOICES}

    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise ServiceError(f"参数明细第 {index} 行格式无效")

        section = (raw.get("section") or "mysqld").strip()
        if section not in valid_sections:
            raise ServiceError(f"参数明细第 {index} 行配置段无效")

        param_name = (raw.get("param_name") or "").strip()
        if not param_name:
            raise ServiceError(f"参数明细第 {index} 行请填写参数名")
        if len(param_name) > 128:
            raise ServiceError(f"参数名过长: {param_name}")
        if not _PARAM_NAME_RE.match(param_name):
            raise ServiceError(f"参数名格式无效: {param_name}")
        if param_name.lower() in MYSQL_PARAM_TEMPLATE_RESERVED_NAMES:
            raise ServiceError(f"参数名「{param_name}」由平台自动生成，请勿在模板中维护")

        param_value = (raw.get("param_value") or "").strip()
        if not param_value:
            raise ServiceError(f"参数「{param_name}」请填写参数值")
        if len(param_value) > 512:
            raise ServiceError(f"参数值过长: {param_name}")

        default_value = (raw.get("default_value") or "").strip()
        if len(default_value) > 512:
            raise ServiceError(f"参考默认值过长: {param_name}")

        key = (section, param_name.lower())
        if key in seen:
            raise ServiceError(f"参数重复: [{section}] {param_name}")
        seen.add(key)

        sort_order = raw.get("sort_order")
        try:
            sort_order_num = int(sort_order) if sort_order is not None else index
        except (TypeError, ValueError) as exc:
            raise ServiceError(f"参数「{param_name}」排序无效") from exc

        normalized.append({
            "sort_order": sort_order_num,
            "section": section,
            "param_name": param_name,
            "param_value": param_value,
            "default_value": default_value,
            "remark": (raw.get("remark") or "").strip()[:256],
        })

    normalized.sort(key=lambda row: (row["sort_order"], row["param_name"]))
    return normalized


def _clear_default_for_major(major_version: str, *, exclude_id: int | None = None) -> None:
    qs = DbDeployMysqlParamTemplate.objects.filter(
        major_version=major_version,
        is_default=True,
    )
    if exclude_id is not None:
        qs = qs.exclude(pk=exclude_id)
    qs.update(is_default=False)


def get_mysql_param_template_detail(template_id: int) -> dict[str, Any]:
    try:
        obj = DbDeployMysqlParamTemplate.objects.prefetch_related("items").get(id=template_id)
    except DbDeployMysqlParamTemplate.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    return {
        "code": 0,
        "msg": "",
        "data": _serialize_template(obj, include_items=True),
    }


def list_mysql_param_templates(
    *,
    page: int,
    limit: int,
    keyword: str,
    major_version: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    queryset = (
        DbDeployMysqlParamTemplate.objects.annotate(item_count=Count("items"))
        .order_by("major_version", "-is_default", "title", "id")
    )
    if major_version:
        queryset = queryset.filter(major_version=major_version)
    if status:
        queryset = queryset.filter(status=status)
    if keyword:
        queryset = queryset.filter(
            Q(title__icontains=keyword)
            | Q(template_code__icontains=keyword)
            | Q(remark__icontains=keyword),
        )

    paginator = Paginator(queryset, limit)
    page_obj = paginator.get_page(page)
    data = [
        _serialize_template(item, item_count=item.item_count)
        for item in page_obj.object_list
    ]
    return {"code": 0, "msg": "", "count": paginator.count, "data": data}


@transaction.atomic
def create_mysql_param_template(body: dict[str, Any]) -> dict[str, Any]:
    title = (body.get("title") or "").strip()
    if not title:
        raise ServiceError("请填写模板标题")
    if len(title) > 128:
        raise ServiceError("模板标题长度不能超过 128")

    template_code = _validate_template_code(body.get("template_code"))
    if DbDeployMysqlParamTemplate.objects.filter(template_code=template_code).exists():
        raise ServiceError("模板编码已存在")

    major_version = _validate_major_version(body.get("major_version"))
    status = _validate_status(body.get("status"))
    is_default = bool(body.get("is_default"))
    items = _normalize_items(body.get("items"))

    if is_default:
        _clear_default_for_major(major_version)

    obj = DbDeployMysqlParamTemplate.objects.create(
        template_code=template_code,
        title=title,
        major_version=major_version,
        status=status,
        is_default=is_default,
        remark=(body.get("remark") or "").strip(),
    )
    DbDeployMysqlParamTemplateItem.objects.bulk_create([
        DbDeployMysqlParamTemplateItem(template=obj, **item)
        for item in items
    ])
    return {"code": 0, "msg": "添加成功", "id": obj.id}


@transaction.atomic
def update_mysql_param_template(body: dict[str, Any]) -> dict[str, Any]:
    obj_id = body.get("id")
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DbDeployMysqlParamTemplate.objects.get(id=obj_id)
    except DbDeployMysqlParamTemplate.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc

    title = (body.get("title") or "").strip()
    if not title:
        raise ServiceError("请填写模板标题")
    if len(title) > 128:
        raise ServiceError("模板标题长度不能超过 128")

    template_code = _validate_template_code(body.get("template_code"))
    if DbDeployMysqlParamTemplate.objects.filter(template_code=template_code).exclude(pk=obj.id).exists():
        raise ServiceError("模板编码已存在")

    major_version = _validate_major_version(body.get("major_version"))
    status = _validate_status(body.get("status"))
    is_default = bool(body.get("is_default"))
    items = _normalize_items(body.get("items"))

    if is_default:
        _clear_default_for_major(major_version, exclude_id=obj.id)

    obj.title = title
    obj.template_code = template_code
    obj.major_version = major_version
    obj.status = status
    obj.is_default = is_default
    obj.remark = (body.get("remark") or "").strip()
    obj.save()

    obj.items.all().delete()
    DbDeployMysqlParamTemplateItem.objects.bulk_create([
        DbDeployMysqlParamTemplateItem(template=obj, **item)
        for item in items
    ])
    return {"code": 0, "msg": "更新成功"}


def delete_mysql_param_template(obj_id: Any) -> dict[str, Any]:
    if not obj_id:
        raise ServiceError("参数不完整")
    try:
        obj = DbDeployMysqlParamTemplate.objects.get(id=obj_id)
    except DbDeployMysqlParamTemplate.DoesNotExist as exc:
        raise ServiceError("记录不存在", 404) from exc
    obj.delete()
    return {"code": 0, "msg": "删除成功"}
