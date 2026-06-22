"""数据库部署常量。"""

from __future__ import annotations

from typing import Any

DEPLOY_STEPS: list[tuple[str, str]] = [
    ("precheck", "预检查"),
    ("prepare", "环境准备"),
    ("install", "安装软件"),
    ("configure", "配置文件"),
    ("initialize", "初始化实例"),
    ("start", "启动服务"),
    ("post_config", "后置配置"),
    ("verify", "连通验证"),
    ("register_cmdb", "注册台账"),
]

JOB_TYPE_CHOICES: list[tuple[str, str]] = [
    ("mysql_standalone", "MySQL 单实例"),
    ("oracle_standalone", "Oracle 单实例"),
]

JOB_TYPE_ENGINE_MAP: dict[str, str] = {
    "mysql_standalone": "mysql",
    "oracle_standalone": "oracle",
}

JOB_TYPE_PLAYBOOK_MAP: dict[str, str] = {
    "mysql_standalone": "mysql/standalone/site.yml",
    "oracle_standalone": "oracle/standalone/site.yml",
}

MYSQL_BINARY_BASEDIR = "/usr/local/mysql"
MYSQL_SERVER_ID_MAX = 4294967295


def _coerce_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def build_mysql_server_id(connect_host: str, port: int) -> int:
    """按 IP 后两段与端口拼接 server_id，如 10.32.13.98 + 3306 -> 13983306。"""
    from .services import ServiceError

    port_num = int(port) if port else 3306
    host = (connect_host or "").strip()
    parts = host.split(".")
    if len(parts) >= 4:
        try:
            third = int(parts[2])
            fourth = int(parts[3])
        except ValueError as exc:
            raise ServiceError(f"连接地址无效，无法生成 server_id: {host}") from exc
        server_id = int(f"{third}{fourth}{port_num}")
    else:
        server_id = port_num
    if server_id < 1 or server_id > MYSQL_SERVER_ID_MAX:
        raise ServiceError(f"server_id 超出有效范围 (1-{MYSQL_SERVER_ID_MAX}): {server_id}")
    return server_id


def build_mysql_install_paths(port: int) -> dict[str, str]:
    """按端口生成 MySQL 实例目录与配置文件路径（basedir 固定为二进制安装路径）。"""
    port_num = int(port) if port else 3306
    instance_root = f"/data/mysql{port_num}"
    binlog_dir = f"{instance_root}/binlog"
    return {
        "basedir": MYSQL_BINARY_BASEDIR,
        "instance_root": instance_root,
        "datadir": f"{instance_root}/data",
        "socket": f"{instance_root}/mysql.sock",
        "cnf_path": f"{instance_root}/my.cnf",
        "log_error": f"{instance_root}/mysql_err.log",
        "binlog_dir": binlog_dir,
        "log_bin": f"{binlog_dir}/mysql-bin",
        "service_name": f"mysqld{port_num}",
    }


def finalize_mysql_deploy_params(merged: dict[str, Any]) -> None:
    """合并 MySQL 安装路径、Binlog/GTID 与 server_id 等运行参数。"""
    from .services import ServiceError

    port = int(merged.get("cmdb", {}).get("port") or 3306)
    connect_host = (merged.get("cmdb", {}).get("connect_host") or "").strip()
    merged.setdefault("install", {})
    merged["install"].update(build_mysql_install_paths(port))

    merged.setdefault("config", {})
    config = merged["config"]
    enable_binlog = _coerce_bool(config.get("enable_binlog"), default=True)
    enable_gtid = _coerce_bool(config.get("enable_gtid"), default=True)
    if enable_gtid:
        enable_binlog = True
    if enable_gtid and not enable_binlog:
        raise ServiceError("开启 GTID 必须先开启 Binlog")

    config["enable_binlog"] = enable_binlog
    config["enable_gtid"] = enable_gtid if enable_binlog else False
    if enable_binlog:
        config.setdefault("binlog_format", "ROW")
        if not config.get("server_id"):
            if not connect_host:
                raise ServiceError("请填写连接地址以生成 server_id")
            config["server_id"] = build_mysql_server_id(connect_host, port)
        else:
            server_id = int(config["server_id"])
            if server_id < 1 or server_id > MYSQL_SERVER_ID_MAX:
                raise ServiceError(f"server_id 超出有效范围 (1-{MYSQL_SERVER_ID_MAX}): {server_id}")
    else:
        config.pop("server_id", None)
