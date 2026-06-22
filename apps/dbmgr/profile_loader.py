"""Version Profile YAML 加载与参数合并。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings

from .deploy_constants import JOB_TYPE_ENGINE_MAP, finalize_mysql_deploy_params
from .services import ServiceError

_PROFILE_CACHE: dict[str, dict[str, Any]] | None = None


def _profiles_root() -> Path:
    return Path(settings.BASE_DIR) / "deploy" / "profiles"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _apply_media_env_override(profile: dict[str, Any]) -> dict[str, Any]:
    data = deepcopy(profile)
    engine = data.get("engine", "")
    if engine == "mysql":
        override = getattr(settings, "DEPLOY_MYSQL_MEDIA_BASE_URL", "") or ""
        if override:
            data["media_base_url"] = override.rstrip("/") + "/"
    elif engine == "oracle":
        override = getattr(settings, "DEPLOY_ORACLE_MEDIA_BASE_URL", "") or ""
        if override:
            data["media_base_url"] = override.rstrip("/") + "/"
    return data


def build_media_info(profile: dict[str, Any]) -> dict[str, Any]:
    base_url = (profile.get("media_base_url") or "").rstrip("/")
    subdir = (profile.get("media_subdir") or "").strip("/")
    filename = profile.get("package_filename") or ""
    if not base_url or not filename:
        raise ServiceError("Profile 缺少介质配置")
    if subdir:
        download_url = f"{base_url}/{subdir}/{filename}"
    else:
        download_url = f"{base_url}/{filename}"
    return {
        "install_method": profile.get("install_method", ""),
        "base_url": base_url + ("/" + subdir if subdir else ""),
        "subdir": subdir,
        "filename": filename,
        "download_url": download_url,
        "package_ref": profile.get("package_ref", ""),
    }


def _load_profile_file(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ServiceError(f"Profile 格式错误: {path}")
    return data


def load_all_profiles(*, refresh: bool = False) -> dict[str, dict[str, Any]]:
    global _PROFILE_CACHE
    if _PROFILE_CACHE is not None and not refresh:
        return _PROFILE_CACHE

    profiles: dict[str, dict[str, Any]] = {}
    root = _profiles_root()
    if not root.exists():
        _PROFILE_CACHE = profiles
        return profiles

    for path in sorted(root.rglob("*.yml")):
        profile = _apply_media_env_override(_load_profile_file(path))
        code = profile.get("profile_code")
        if not code:
            continue
        profiles[str(code)] = profile

    _PROFILE_CACHE = profiles
    return profiles


def load_profile(profile_code: str) -> dict[str, Any]:
    profiles = load_all_profiles()
    profile = profiles.get(profile_code)
    if not profile:
        raise ServiceError(f"未找到版本档案: {profile_code}")
    if profile.get("status") == "disabled":
        raise ServiceError(f"版本档案已禁用: {profile_code}")
    return deepcopy(profile)


def list_profiles(
    *,
    engine: str | None = None,
    job_type: str | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for profile in load_all_profiles().values():
        if engine and profile.get("engine") != engine:
            continue
        if job_type:
            supported = profile.get("supported_job_types") or []
            if job_type not in supported:
                continue
        if profile.get("status") == "disabled":
            continue
        items.append({
            "profile_code": profile.get("profile_code", ""),
            "display_name": profile.get("display_name", ""),
            "engine": profile.get("engine", ""),
            "major_version": profile.get("major_version", ""),
            "minor_version": profile.get("minor_version", ""),
            "supported_job_types": profile.get("supported_job_types", []),
            "default_params": profile.get("default_params", {}),
            "min_memory_gb": profile.get("min_memory_gb"),
        })
    items.sort(key=lambda item: (item["engine"], item["profile_code"]))
    return items


def resolve_deploy_params(
    *,
    job_type: str,
    profile_code: str,
    user_params: dict[str, Any],
    host_id: int,
) -> dict[str, Any]:
    from apps.common.models import Host, HostIP

    profile = load_profile(profile_code)
    supported = profile.get("supported_job_types") or []
    if job_type not in supported:
        raise ServiceError("所选版本档案不支持当前部署类型")

    engine = JOB_TYPE_ENGINE_MAP.get(job_type, "")
    if profile.get("engine") != engine:
        raise ServiceError("版本档案与部署类型不匹配")

    try:
        host = Host.objects.select_related("os_type").get(id=host_id)
    except Host.DoesNotExist as exc:
        raise ServiceError("目标主机不存在") from exc

    business_ip = (
        HostIP.objects.filter(host_id=host_id, ip_type="business")
        .order_by("id")
        .values_list("ip_address", flat=True)
        .first()
    )
    if not business_ip:
        business_ip = (
            HostIP.objects.filter(host_id=host_id)
            .order_by("id")
            .values_list("ip_address", flat=True)
            .first()
        )

    merged = _deep_merge(profile.get("default_params") or {}, user_params)
    merged.setdefault("meta", {})
    merged["meta"].update({
        "job_type": job_type,
        "version_profile_code": profile_code,
        "engine": engine,
        "playbook_variant": profile.get("playbook_variant", ""),
    })
    merged.setdefault("target", {})
    merged["target"]["host_id"] = host_id
    merged["target"]["hostname"] = host.hostname
    merged["target"]["os_type"] = host.os_type.name if host.os_type_id else ""
    merged.setdefault("cmdb", {})
    if business_ip and not merged["cmdb"].get("connect_host"):
        merged["cmdb"]["connect_host"] = business_ip
    merged["profile"] = {
        "profile_code": profile_code,
        "display_name": profile.get("display_name", ""),
        "major_version": profile.get("major_version", ""),
        "minor_version": profile.get("minor_version", ""),
    }
    merged["media"] = build_media_info(profile)
    if engine == "mysql":
        finalize_mysql_deploy_params(merged)
    return merged
