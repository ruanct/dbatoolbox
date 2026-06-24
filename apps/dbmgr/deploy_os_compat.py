"""部署目标主机操作系统与 Profile supported_os_rules 校验。"""
from __future__ import annotations

import re
from typing import Any

from .services import ServiceError

_OS_MAJOR_VERSION_PATTERN = re.compile(r"(\d+)(?:\.\d+)*")

_DISTRIBUTION_ID_FAMILY_MAP: dict[str, str] = {
    "alinux": "alinux",
    "anolis": "anolis",
    "centos": "centos",
    "rhel": "rhel",
    "rocky": "rocky",
}

_DISTRIBUTION_FAMILY_MAP: dict[str, str] = {
    "centos": "centos",
    "redhat": "rhel",
    "red hat enterprise linux": "rhel",
    "anolis": "anolis",
    "anolis os": "anolis",
    "anolis linux": "anolis",
    "alibaba": "alinux",
    "alibaba cloud linux": "alinux",
    "alinux": "alinux",
    "aliyun linux": "alinux",
    "aliyun": "alinux",
}

_HOST_OS_TYPE_FAMILY_MAP: dict[str, str] = {
    "centos": "centos",
    "rhel": "rhel",
    "red hat": "rhel",
    "red hat enterprise linux": "rhel",
    "anolis": "anolis",
    "anolis os": "anolis",
    "龙蜥": "anolis",
    "alibaba": "alinux",
    "alibaba cloud linux": "alinux",
    "alinux": "alinux",
    "aliyun linux": "alinux",
    "阿里云linux": "alinux",
    "阿里云 linux": "alinux",
}

_DEFAULT_OS_REQUIREMENT_DESC = (
    "CentOS>=7、RHEL>=7、Anolis>=7、阿里云 Linux>=3"
)

_FAMILY_LABELS: dict[str, str] = {
    "centos": "CentOS",
    "rhel": "RHEL",
    "anolis": "Anolis",
    "alinux": "阿里云 Linux",
}


def normalize_os_family(
    *,
    distribution: str = "",
    distribution_id: str = "",
) -> str:
    """将 ansible_distribution / distribution_id 归一化为 os family。"""
    dist_id = (distribution_id or "").strip().lower()
    if dist_id in _DISTRIBUTION_ID_FAMILY_MAP:
        return _DISTRIBUTION_ID_FAMILY_MAP[dist_id]
    return normalize_distribution(distribution)


def normalize_distribution(distribution: str) -> str:
    """将 ansible_distribution 等字符串归一化为 os family。"""
    key = (distribution or "").strip().lower()
    return _DISTRIBUTION_FAMILY_MAP.get(key, "unknown")


def normalize_host_os_type(os_type_name: str) -> str:
    """将 CMDB 主机 OS 类型名归一化为 os family。"""
    key = (os_type_name or "").strip().lower()
    return _HOST_OS_TYPE_FAMILY_MAP.get(key, "unknown")


def parse_os_major_version(os_version: str) -> int | None:
    """从 OS 版本字符串解析 major，如 7.9、CentOS 7.9 -> 7。"""
    text = (os_version or "").strip()
    if not text:
        return None
    head = text.split(".", 1)[0]
    try:
        return int(head)
    except ValueError:
        pass
    match = _OS_MAJOR_VERSION_PATTERN.search(text)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def is_os_supported(
    family: str,
    major: int | None,
    rules: list[dict[str, Any]],
) -> bool:
    """判断 family + major 是否命中任一 supported_os_rules。"""
    if not rules:
        return True
    if family == "unknown" or major is None:
        return False
    for rule in rules:
        if str(rule.get("family") or "") != family:
            continue
        try:
            min_major = int(rule.get("min_major", 0))
        except (TypeError, ValueError):
            continue
        if major < min_major:
            continue
        max_major_raw = rule.get("max_major")
        if max_major_raw is not None and max_major_raw != "":
            try:
                max_major = int(max_major_raw)
            except (TypeError, ValueError):
                continue
            if major > max_major:
                continue
        return True
    return False


def format_supported_os_rules(rules: list[dict[str, Any]]) -> str:
    """将规则格式化为可读说明。"""
    if not rules:
        return _DEFAULT_OS_REQUIREMENT_DESC
    parts: list[str] = []
    for rule in rules:
        family = str(rule.get("family") or "")
        label = _FAMILY_LABELS.get(family, family)
        min_major = rule.get("min_major", "?")
        max_major = rule.get("max_major")
        if max_major is not None and max_major != "":
            if str(max_major) == str(min_major):
                parts.append(f"{label} {min_major}")
            else:
                parts.append(f"{label} {min_major}-{max_major}")
        else:
            parts.append(f"{label}>={min_major}")
    return "、".join(parts) if parts else _DEFAULT_OS_REQUIREMENT_DESC


def validate_host_os_against_profile(host: Any, profile: dict[str, Any]) -> None:
    """创建任务时静态校验：CMDB 主机 OS 是否满足 Profile 规则。"""
    rules = profile.get("supported_os_rules") or []
    if not rules:
        return
    os_type_name = host.os_type.name if getattr(host, "os_type_id", None) else ""
    family = normalize_host_os_type(os_type_name)
    major = parse_os_major_version(getattr(host, "os_version", "") or "")
    if is_os_supported(family, major, rules):
        return
    requirement = format_supported_os_rules(rules)
    raise ServiceError(
        "目标主机操作系统不在 Profile 支持范围内："
        f"{os_type_name or '-'} {getattr(host, 'os_version', '') or '-'}；"
        f"要求 {requirement}"
    )
