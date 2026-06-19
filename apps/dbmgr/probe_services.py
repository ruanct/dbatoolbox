"""数据库实例存活探测。"""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Any

import mysql.connector
from django.db.models import Prefetch
from django.utils import timezone

from .models import DatabaseAccount, DatabaseInstance, DatabaseInstanceHost

PROBE_STATUS_LABELS = dict(DatabaseInstance.PROBE_STATUS_CHOICES)
PROBE_TCP_TIMEOUT = 3
PROBE_MYSQL_TIMEOUT = 3


@dataclass
class ProbeResult:
    status: str
    message: str
    latency_ms: int | None = None


def _tcp_reachable(host: str, port: int, *, timeout: int = PROBE_TCP_TIMEOUT) -> bool:
    if not host:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _get_default_account(instance: DatabaseInstance) -> DatabaseAccount | None:
    return instance.accounts.filter(is_default=True).first() or instance.accounts.order_by("id").first()


def _build_host_ip_map(host_ids: set[int]) -> dict[int, str]:
    if not host_ids:
        return {}
    from apps.common.models import HostIP

    ip_map: dict[int, str] = {}
    for ip in HostIP.objects.filter(host_id__in=host_ids).order_by("host_id", "id"):
        if ip.host_id not in ip_map:
            ip_map[ip.host_id] = ip.ip_address
    return ip_map


def resolve_deploy_host_endpoint(
    deploy_host: DatabaseInstanceHost,
    *,
    host_ip_map: dict[int, str] | None = None,
) -> tuple[str, int]:
    connect_host = (deploy_host.listener_host or "").strip()
    if not connect_host:
        if host_ip_map is not None:
            connect_host = host_ip_map.get(deploy_host.host_id, "")
        else:
            from apps.common.models import HostIP

            first_ip = HostIP.objects.filter(host_id=deploy_host.host_id).order_by("id").first()
            connect_host = first_ip.ip_address if first_ip else ""
    port = deploy_host.listener_port or deploy_host.instance.port
    return connect_host, port


def _probe_mysql_endpoint(
    *,
    host: str,
    port: int,
    account: DatabaseAccount,
    instance: DatabaseInstance,
) -> ProbeResult:
    db_name = (account.default_schema or instance.db_name or "").strip() or None
    connect_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "user": account.account_name,
        "password": account.account_pswd,
        "connection_timeout": PROBE_MYSQL_TIMEOUT,
    }
    if db_name:
        connect_kwargs["database"] = db_name
    try:
        conn = mysql.connector.connect(**connect_kwargs)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        finally:
            conn.close()
        return ProbeResult("alive", "连接正常")
    except mysql.connector.Error as exc:
        return ProbeResult("dead", f"MySQL连接失败: {exc}")


def _probe_mysql_deep(instance: DatabaseInstance, account: DatabaseAccount) -> ProbeResult:
    return _probe_mysql_endpoint(
        host=instance.connect_host,
        port=instance.port,
        account=account,
        instance=instance,
    )


def probe_instance(instance: DatabaseInstance) -> ProbeResult:
    if instance.status == "maintenance":
        return ProbeResult("maintenance", "实例处于维护状态")

    started = time.monotonic()
    if not _tcp_reachable(instance.connect_host, instance.port):
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult("dead", "端口不可达", latency_ms)

    if instance.engine == "mysql":
        account = _get_default_account(instance)
        if account:
            result = _probe_mysql_deep(instance, account)
            if result.latency_ms is None:
                result.latency_ms = int((time.monotonic() - started) * 1000)
            return result

    latency_ms = int((time.monotonic() - started) * 1000)
    return ProbeResult("alive", "端口可达", latency_ms)


def probe_deploy_host(
    deploy_host: DatabaseInstanceHost,
    *,
    host_ip_map: dict[int, str] | None = None,
) -> ProbeResult:
    instance = deploy_host.instance
    if instance.status == "maintenance":
        return ProbeResult("maintenance", "所属实例处于维护状态")

    connect_host, port = resolve_deploy_host_endpoint(deploy_host, host_ip_map=host_ip_map)
    if not connect_host:
        return ProbeResult("dead", "未配置节点连接地址")

    started = time.monotonic()
    if not _tcp_reachable(connect_host, port):
        latency_ms = int((time.monotonic() - started) * 1000)
        return ProbeResult("dead", "节点端口不可达", latency_ms)

    if instance.engine == "mysql":
        account = _get_default_account(instance)
        if account:
            result = _probe_mysql_endpoint(
                host=connect_host,
                port=port,
                account=account,
                instance=instance,
            )
            if result.latency_ms is None:
                result.latency_ms = int((time.monotonic() - started) * 1000)
            return result

    latency_ms = int((time.monotonic() - started) * 1000)
    return ProbeResult("alive", "节点端口可达", latency_ms)


def save_probe_result(instance: DatabaseInstance, result: ProbeResult) -> None:
    instance.probe_status = result.status
    instance.probe_message = result.message
    instance.latency_ms = result.latency_ms
    instance.last_probed_at = timezone.now()
    instance.save(update_fields=["probe_status", "probe_message", "latency_ms", "last_probed_at"])


def save_deploy_host_probe_result(deploy_host: DatabaseInstanceHost, result: ProbeResult) -> None:
    deploy_host.probe_status = result.status
    deploy_host.probe_message = result.message
    deploy_host.latency_ms = result.latency_ms
    deploy_host.last_probed_at = timezone.now()
    deploy_host.save(update_fields=["probe_status", "probe_message", "latency_ms", "last_probed_at"])


def probe_and_save_instance(instance: DatabaseInstance) -> ProbeResult:
    result = probe_instance(instance)
    save_probe_result(instance, result)
    return result


def probe_and_save_deploy_host(
    deploy_host: DatabaseInstanceHost,
    *,
    host_ip_map: dict[int, str] | None = None,
) -> ProbeResult:
    result = probe_deploy_host(deploy_host, host_ip_map=host_ip_map)
    save_deploy_host_probe_result(deploy_host, result)
    return result


def _accumulate_summary(summary: dict[str, int], result: ProbeResult) -> None:
    summary["total"] += 1
    if result.status in summary:
        summary[result.status] += 1
    else:
        summary["unknown"] += 1


def probe_all_instances() -> dict[str, Any]:
    instances = list(
        DatabaseInstance.objects.prefetch_related(
            "accounts",
            Prefetch(
                "deploy_hosts",
                queryset=DatabaseInstanceHost.objects.select_related("instance", "host").order_by(
                    "sort_order", "id",
                ),
            ),
        ).order_by("id"),
    )
    host_ids = {
        deploy_host.host_id
        for instance in instances
        for deploy_host in instance.deploy_hosts.all()
    }
    host_ip_map = _build_host_ip_map(host_ids)

    summary = {"total": 0, "alive": 0, "dead": 0, "maintenance": 0, "unknown": 0}
    for instance in instances:
        result = probe_and_save_instance(instance)
        _accumulate_summary(summary, result)

        if instance.topology != "ha_cluster":
            continue
        for deploy_host in instance.deploy_hosts.all():
            node_result = probe_and_save_deploy_host(deploy_host, host_ip_map=host_ip_map)
            _accumulate_summary(summary, node_result)

    return {
        "code": 0,
        "msg": "探测完成",
        "summary": summary,
        "probed_at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
