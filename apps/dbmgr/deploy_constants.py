"""数据库部署常量。"""

from __future__ import annotations

import hashlib
import re
from typing import Any

_IPV4_ADDRESS_RE = re.compile(
    r"^(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)$"
)

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

MYSQL_REPLICA_DEPLOY_STEPS: list[tuple[str, str]] = [
    ("precheck", "预检查"),
    ("prepare", "环境准备"),
    ("install", "安装软件"),
    ("configure", "配置文件"),
    ("initialize", "初始化实例"),
    ("start", "启动服务"),
    ("repl_bootstrap", "全量同步"),
    ("repl_setup", "建立复制"),
    ("repl_readonly", "开启只读"),
    ("repl_verify", "复制验收"),
    ("verify", "连通验证"),
    ("register_cmdb", "注册台账"),
]

# Ansible 单步 subprocess 超时（秒）
DEPLOY_ANSIBLE_STEP_TIMEOUT_DEFAULT = 3600
DEPLOY_ANSIBLE_STEP_TIMEOUT_REPL_BOOTSTRAP = 7200

DEPLOY_ANSIBLE_STEP_TIMEOUTS: dict[str, int] = {
    "repl_bootstrap": DEPLOY_ANSIBLE_STEP_TIMEOUT_REPL_BOOTSTRAP,
}


def resolve_deploy_step_timeout(step_code: str) -> int:
    """按 step_code 返回 Ansible 步骤超时；未配置则用默认 1 小时。"""
    return DEPLOY_ANSIBLE_STEP_TIMEOUTS.get(step_code, DEPLOY_ANSIBLE_STEP_TIMEOUT_DEFAULT)


MYSQL_REPLICA_MASTER_RUNTIME_KEYS: tuple[str, ...] = (
    "lower_case_table_names",
    "binlog_checksum",
    "binlog_format",
    "gtid_mode",
    "enforce_gtid_consistency",
    "log_slave_updates",
    "character_set_server",
    "collation_server",
    "default_authentication_plugin",
    "transaction_write_set_extraction",
    "sql_mode",
)

JOB_TYPE_CHOICES: list[tuple[str, str]] = [
    ("mysql_standalone", "MySQL 单实例"),
    ("mysql_replica", "MySQL 从库"),
    ("oracle_standalone", "Oracle 单实例"),
]

STANDALONE_DEPLOY_JOB_TYPES: list[tuple[str, str]] = [
    ("mysql_standalone", "MySQL 单实例"),
    ("oracle_standalone", "Oracle 单实例"),
]

JOB_TYPE_ENGINE_MAP: dict[str, str] = {
    "mysql_standalone": "mysql",
    "mysql_replica": "mysql",
    "oracle_standalone": "oracle",
}

# 与 ensure_mysql_server_id_available 一致：进行中任务仍视为占用端点
DEPLOY_JOB_ACTIVE_STATUSES: tuple[str, ...] = (
    "pending",
    "prechecking",
    "running",
    "verifying",
    "failed",
)

# 同主机部署互斥：以下状态表示目标机已有部署任务占用
HOST_DEPLOY_ACTIVE_STATUSES: tuple[str, ...] = (
    "pending",
    "prechecking",
    "running",
    "verifying",
)

JOB_TYPE_PLAYBOOK_MAP: dict[str, str] = {
    "mysql_standalone": "mysql/standalone/site.yml",
    "mysql_replica": "mysql/replica/site.yml",
    "oracle_standalone": "oracle/standalone/site.yml",
}

MYSQL_BINARY_BASEDIR = "/usr/local/mysql"
MYSQL_SERVER_ID_MAX = 4294967295
MYSQL_ROOT_GRANT_HOST = "localhost"
MYSQL_DBA_ACCOUNT_TYPE = "user_dba"
MYSQL_DBA_ACCOUNT_NAME = "dba_admin"
# 全量导入 mysqldump 单行可能很大，须大于默认 4M
MYSQL_DEFAULT_MAX_ALLOWED_PACKET = "1024M"
MYSQL_IMPORT_MAX_ALLOWED_PACKET_BYTES = 1073741824

MYSQL_PARAM_TEMPLATE_MAJOR_CHOICES: list[tuple[str, str]] = [
    ("5.7", "MySQL 5.7"),
    ("8.0", "MySQL 8.0"),
]

MYSQL_PARAM_TEMPLATE_STATUS_CHOICES: list[tuple[str, str]] = [
    ("enabled", "启用"),
    ("disabled", "禁用"),
]

# my.cnf 中由平台/Playbook 派生或固定写入，禁止在参数模板中维护
MYSQL_PARAM_TEMPLATE_RESERVED_NAMES: frozenset[str] = frozenset({
    "basedir",
    "datadir",
    "port",
    "socket",
    "server_id",
    "log-error",
    "log_error",
    "pid-file",
    "pid_file",
    "bind-address",
    "bind_address",
    "log_bin",
    "log-bin",
    "tmpdir",
    "slow-query-log-file",
    "slow_query_log_file",
})
MYSQL_ROOT_ACCOUNT_TYPE = "user_adm"
MYSQL_DBA_GRANT_GROUPS: list[str] = [
    "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, RELOAD, PROCESS, REFERENCES, INDEX, ALTER",
    "SHOW DATABASES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, REPLICATION SLAVE, REPLICATION CLIENT",
    "CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, CREATE USER, EVENT, TRIGGER",
]
MYSQL_DBA_GRANT_GROUPS_80_EXTRA: list[str] = [
    "CREATE ROLE, DROP ROLE",
]


def _parse_major_minor(major_version: str) -> tuple[int, int]:
    parts = (major_version or "5.7").strip().split(".")
    try:
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return 5, 7


def build_mysql_dba_global_privileges(major_version: str) -> str:
    """按 MySQL major 版本生成高级 DBA 账号全局授权列表（可读说明用）。"""
    groups = list(MYSQL_DBA_GRANT_GROUPS)
    if _parse_major_minor(major_version) >= (8, 0):
        groups.extend(MYSQL_DBA_GRANT_GROUPS_80_EXTRA)
    return ", ".join(groups)


def build_mysql_dba_grant_statements(major_version: str) -> list[str]:
    """生成高级 DBA 账号 GRANT SQL（按权限分组，避免 MySQL 语法歧义）。"""
    groups = list(MYSQL_DBA_GRANT_GROUPS)
    if _parse_major_minor(major_version) >= (8, 0):
        groups.extend(MYSQL_DBA_GRANT_GROUPS_80_EXTRA)

    statements = [
        f"GRANT {group} ON *.* TO '__DBA_USER__'@'__DBA_HOST__';"
        for group in groups
    ]
    statements.append(
        "GRANT USAGE ON *.* TO '__DBA_USER__'@'__DBA_HOST__' WITH GRANT OPTION;"
    )
    return statements


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


def is_ipv4_address(host: str) -> bool:
    """判断是否为 IPv4 点分十进制地址。"""
    return bool(_IPV4_ADDRESS_RE.match((host or "").strip()))


def get_host_business_ip(host_id: int) -> str:
    """读取目标主机业务 IP（HostIP.ip_type=business）。"""
    from apps.common.models import HostIP

    ip = (
        HostIP.objects.filter(host_id=host_id, ip_type="business")
        .order_by("id")
        .values_list("ip_address", flat=True)
        .first()
    )
    return (ip or "").strip()


def validate_mysql_deploy_connect_host(host_id: int, connect_host: str) -> str:
    """MySQL 部署连接地址须为目标主机业务 IPv4（禁止 VIP/域名）。"""
    from .services import ServiceError

    business_ip = get_host_business_ip(host_id)
    if not business_ip:
        raise ServiceError("目标主机未维护业务 IP，请先在主机台账补充")
    if not is_ipv4_address(business_ip):
        raise ServiceError(f"目标主机业务 IP 格式无效: {business_ip}")

    submitted = (connect_host or "").strip()
    if submitted and submitted != business_ip:
        raise ServiceError(f"连接地址须为目标主机业务 IP: {business_ip}")
    return business_ip


def _validate_server_id_range(server_id: int) -> int:
    from .services import ServiceError

    if server_id < 1 or server_id > MYSQL_SERVER_ID_MAX:
        raise ServiceError(f"server_id 超出有效范围 (1-{MYSQL_SERVER_ID_MAX}): {server_id}")
    return server_id


def build_mysql_server_id(connect_host: str, port: int) -> int:
    """由 connect_host:port 的 SHA256 摘要生成 server_id（1..4294967295）。"""
    from .services import ServiceError

    host = (connect_host or "").strip()
    if not is_ipv4_address(host):
        raise ServiceError(f"连接地址无效，无法生成 server_id: {host or '-'}")

    port_num = int(port) if port else 3306
    digest = hashlib.sha256(f"{host}:{port_num}".encode()).digest()
    raw = int.from_bytes(digest[:4], "big")
    return _validate_server_id_range((raw % (MYSQL_SERVER_ID_MAX - 1)) + 1)


def ensure_deploy_endpoint_available(
    *,
    engine: str,
    connect_host: str,
    port: int,
    db_name: str = "",
    exclude_job_id: int | None = None,
) -> None:
    """校验 CMDB 端点 (engine, connect_host, port, db_name) 未被台账或其它进行中任务占用。"""
    from .models import DatabaseInstance, DbDeployJob
    from .services import ServiceError

    host = (connect_host or "").strip()
    if not host:
        raise ServiceError("连接地址无效，无法校验端点唯一性")
    port_num = int(port) if port else 3306
    db = (db_name or "").strip()

    conflict_instance = (
        DatabaseInstance.objects.filter(
            engine=engine,
            connect_host=host,
            port=port_num,
            db_name=db,
        )
        .values_list("instance_name", flat=True)
        .first()
    )
    if conflict_instance:
        raise ServiceError(
            f"连接端点 {host}:{port_num} 已被实例「{conflict_instance}」使用，请调整端口或连接地址"
        )

    jobs = DbDeployJob.objects.filter(status__in=DEPLOY_JOB_ACTIVE_STATUSES).only(
        "id", "job_type", "resolved_params"
    )
    if exclude_job_id is not None:
        jobs = jobs.exclude(pk=exclude_job_id)

    for job in jobs:
        job_engine = JOB_TYPE_ENGINE_MAP.get(job.job_type, "")
        if job_engine != engine:
            continue
        cmdb = (job.resolved_params or {}).get("cmdb") or {}
        job_host = (cmdb.get("connect_host") or "").strip()
        job_port = int(cmdb.get("port") or 0)
        job_db = (cmdb.get("db_name") or "").strip()
        if job_host == host and job_port == port_num and job_db == db:
            raise ServiceError(
                f"连接端点 {host}:{port_num} 与进行中的部署任务 #{job.id} 冲突，请调整端口或稍后重试"
            )


def ensure_host_deploy_lock_available(
    host_id: int,
    *,
    exclude_job_id: int | None = None,
) -> None:
    """校验目标主机无其它进行中的部署任务（同机部署互斥）。"""
    from .models import DbDeployJob
    from .services import ServiceError

    jobs = DbDeployJob.objects.filter(
        target_host_id=host_id,
        status__in=HOST_DEPLOY_ACTIVE_STATUSES,
    ).only("id", "job_type", "status")
    if exclude_job_id is not None:
        jobs = jobs.exclude(pk=exclude_job_id)

    conflict = jobs.order_by("-id").first()
    if conflict:
        raise ServiceError(
            f"目标主机已有进行中的部署任务 #{conflict.id}"
            f"（{conflict.get_status_display()}），请待其结束后再创建新任务"
        )


def ensure_mysql_server_id_available(
    server_id: int,
    *,
    exclude_job_id: int | None = None,
) -> None:
    """校验 server_id 未被台账实例或其它进行中部署任务占用。"""
    from .models import DatabaseInstance, DbDeployJob
    from .services import ServiceError

    server_id = _validate_server_id_range(int(server_id))

    conflict_instance = (
        DatabaseInstance.objects.filter(engine="mysql", server_id=server_id)
        .values_list("instance_name", flat=True)
        .first()
    )
    if conflict_instance:
        raise ServiceError(
            f"server_id {server_id} 已被实例「{conflict_instance}」使用，请调整端口或联系 DBA"
        )

    jobs = DbDeployJob.objects.filter(
        job_type__in=("mysql_standalone", "mysql_replica"),
        status__in=DEPLOY_JOB_ACTIVE_STATUSES,
    ).only("id", "resolved_params")
    if exclude_job_id is not None:
        jobs = jobs.exclude(pk=exclude_job_id)

    for job in jobs:
        config = (job.resolved_params or {}).get("config") or {}
        job_server_id = config.get("server_id")
        if job_server_id is not None and int(job_server_id) == server_id:
            raise ServiceError(
                f"server_id {server_id} 与进行中的部署任务 #{job.id} 冲突，请调整端口或稍后重试"
            )


def build_mysql_install_paths(port: int) -> dict[str, str]:
    """按端口生成 MySQL 实例目录与配置文件路径（basedir 固定为二进制安装路径）。"""
    port_num = int(port) if port else 3306
    instance_root = f"/data/mysql/db{port_num}"
    binlog_dir = f"{instance_root}/binlog"
    return {
        "basedir": MYSQL_BINARY_BASEDIR,
        "instance_root": instance_root,
        "datadir": f"{instance_root}/data",
        "tmpdir": f"{instance_root}/tmp",
        "socket": f"{instance_root}/mysql.sock",
        "cnf_path": f"{instance_root}/my.cnf",
        "log_error": f"{instance_root}/mysql_err.log",
        "slow_query_log_file": f"{instance_root}/slow_query.log",
        "binlog_dir": binlog_dir,
        "log_bin": f"{binlog_dir}/mysql-bin",
        "service_name": f"mysqld{port_num}",
    }


def finalize_mysql_deploy_params(merged: dict[str, Any]) -> None:
    """合并 MySQL 安装路径、Binlog/GTID 与 server_id 等运行参数。"""
    from .services import ServiceError

    port = int(merged.get("cmdb", {}).get("port") or 3306)
    connect_host = (merged.get("cmdb", {}).get("connect_host") or "").strip()
    db_name = (merged.get("cmdb", {}).get("db_name") or "").strip()
    ensure_deploy_endpoint_available(
        engine="mysql",
        connect_host=connect_host,
        port=port,
        db_name=db_name,
        exclude_job_id=merged.get("meta", {}).get("deploy_job_id"),
    )
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
            config["server_id"] = _validate_server_id_range(int(config["server_id"]))
        ensure_mysql_server_id_available(
            int(config["server_id"]),
            exclude_job_id=merged.get("meta", {}).get("deploy_job_id"),
        )
    else:
        config.pop("server_id", None)

    merged.setdefault("credentials", {})
    admin_account = merged["credentials"].setdefault("admin_account", {})
    admin_account.setdefault("account_name", MYSQL_DBA_ACCOUNT_NAME)
    admin_account["account_type"] = MYSQL_DBA_ACCOUNT_TYPE
    major_version = str((merged.get("profile") or {}).get("major_version") or "5.7")
    merged["credentials"]["dba_global_privileges"] = build_mysql_dba_global_privileges(major_version)
    merged["credentials"]["dba_grant_statements"] = build_mysql_dba_grant_statements(major_version)
    build_mysql_cnf_sections(merged)


def apply_mysql_master_runtime(merged: dict[str, Any]) -> None:
    """将 precheck 采集的主库现场变量合并进从库 config 并重生成 my.cnf 段落。"""
    context = merged.get("context") or {}
    runtime = context.get("master_runtime") or {}
    if not runtime:
        return

    config = merged.setdefault("config", {})
    if runtime.get("character_set_server"):
        config["character_set"] = runtime["character_set_server"]
    if runtime.get("collation_server"):
        config["collation"] = runtime["collation_server"]
    if runtime.get("default_authentication_plugin"):
        config["default_authentication_plugin"] = runtime["default_authentication_plugin"]
    if runtime.get("sql_mode") is not None:
        config["sql_mode"] = runtime["sql_mode"]
    if runtime.get("binlog_format"):
        config["binlog_format"] = runtime["binlog_format"]

    gtid_mode = str(runtime.get("gtid_mode") or "").upper()
    enforce_gtid = str(runtime.get("enforce_gtid_consistency") or "").upper()
    log_bin = str(runtime.get("log_bin") or runtime.get("log_bin_enabled") or "").upper()
    config["enable_binlog"] = log_bin in {"ON", "1", "TRUE", "YES"}
    config["enable_gtid"] = (
        gtid_mode == "ON"
        and enforce_gtid == "ON"
        and config["enable_binlog"]
    )

    for key in MYSQL_REPLICA_MASTER_RUNTIME_KEYS:
        if key in runtime and runtime[key] is not None:
            config[key] = runtime[key]
    # server_id 必须保持从库独立生成值，禁止覆盖为主库 @@server_id

    build_mysql_cnf_sections(merged)


def _cnf_line_key(line: str) -> str:
    name = (line or "").split("=", 1)[0].strip()
    return name.lower().replace("_", "-")


def build_mysql_cnf_sections(merged: dict[str, Any]) -> None:
    """根据合并后的 config/install 生成 my.cnf 各段行列表。"""
    config = merged.setdefault("config", {})
    install = merged.get("install") or {}
    profile_major = str((merged.get("profile") or {}).get("major_version") or "5.7")
    is_mysql80 = _parse_major_minor(profile_major) >= (8, 0)
    is_replica = (merged.get("meta") or {}).get("job_type") == "mysql_replica"

    enable_binlog = _coerce_bool(config.get("enable_binlog"), default=True)
    enable_gtid = _coerce_bool(config.get("enable_gtid"), default=True) and enable_binlog

    character_set = config.get("character_set") or "utf8mb4"
    collation = config.get("collation") or (
        "utf8mb4_0900_ai_ci" if is_mysql80 else "utf8mb4_unicode_ci"
    )
    max_connections = config.get("max_connections") or 500
    innodb_buffer_pool_size = config.get("innodb_buffer_pool_size") or "1G"
    auth_plugin = config.get("default_authentication_plugin") or (
        "caching_sha2_password" if is_mysql80 else "mysql_native_password"
    )
    client_charset = config.get("client_character_set") or character_set
    max_allowed_packet = config.get("max_allowed_packet") or MYSQL_DEFAULT_MAX_ALLOWED_PACKET

    mysqld_lines: list[str] = [
        f"basedir={install.get('basedir', MYSQL_BINARY_BASEDIR)}",
        f"datadir={install.get('datadir', '')}",
        f"port={merged.get('cmdb', {}).get('port', 3306)}",
        f"socket={install.get('socket', '')}",
        f"bind-address={config.get('bind_address') or ('0.0.0.0' if is_replica else '127.0.0.1')}",
        f"character-set-server={character_set}",
        f"collation-server={collation}",
        f"max_connections={max_connections}",
        f"max_allowed_packet={max_allowed_packet}",
        f"innodb_buffer_pool_size={innodb_buffer_pool_size}",
        f"default_authentication_plugin={auth_plugin}",
        f"log-error={install.get('log_error', '')}",
        f"tmpdir={install.get('tmpdir', '')}",
        f"slow_query_log_file={install.get('slow_query_log_file', '')}",
        f"pid-file={install.get('datadir', '')}/mysqld.pid",
        "symbolic-links=0",
        "explicit_defaults_for_timestamp=1",
    ]

    if config.get("sql_mode"):
        mysqld_lines.append(f"sql_mode={config['sql_mode']}")

    if is_replica:
        replica_lines: list[tuple[str, Any]] = [
            ("lower_case_table_names", config.get("lower_case_table_names", 1)),
            ("binlog_checksum", config.get("binlog_checksum", "CRC32")),
            ("slave_sql_verify_checksum", config.get("slave_sql_verify_checksum", "ON")),
        ]
        if is_mysql80:
            replica_lines.append(
                ("transaction_write_set_extraction", config.get("transaction_write_set_extraction", "XXHASH64")),
            )
        for param_name, param_value in replica_lines:
            if param_value is None or param_value == "":
                continue
            mysqld_lines.append(f"{param_name}={param_value}")

    written_keys = {_cnf_line_key(line) for line in mysqld_lines}

    if enable_binlog:
        mysqld_lines.append(f"server_id={config.get('server_id', '')}")
        mysqld_lines.append(f"log_bin={install.get('log_bin', '')}")
        mysqld_lines.append(f"binlog_format={config.get('binlog_format') or 'ROW'}")
        written_keys.update(
            _cnf_line_key(line) for line in mysqld_lines[-3:]
        )
        if enable_gtid:
            for gtid_line in (
                "gtid_mode=ON",
                "enforce_gtid_consistency=ON",
                "log_slave_updates=ON",
            ):
                mysqld_lines.append(gtid_line)
                written_keys.add(_cnf_line_key(gtid_line))
    else:
        mysqld_lines.append("skip-log-bin")
        written_keys.add("skip-log-bin")

    template_items = (config.get("cnf_template_items") or {}).get("mysqld") or []
    for item in template_items:
        param_name = (item.get("param_name") or "").strip()
        param_value = (item.get("param_value") or "").strip()
        if not param_name or not param_value:
            continue
        line_key = _normalize_cnf_param_name(param_name)
        if line_key in written_keys:
            continue
        if line_key in {_normalize_cnf_param_name(x) for x in MYSQL_PARAM_TEMPLATE_RESERVED_NAMES}:
            continue
        mysqld_lines.append(f"{param_name}={param_value}")
        written_keys.add(line_key)

    client_lines: list[str] = [
        f"socket={install.get('socket', '')}",
        f"default-character-set={client_charset}",
        f"max_allowed_packet={max_allowed_packet}",
    ]
    client_written = {_cnf_line_key(line) for line in client_lines}
    client_template_items = (config.get("cnf_template_items") or {}).get("client") or []
    for item in client_template_items:
        param_name = (item.get("param_name") or "").strip()
        param_value = (item.get("param_value") or "").strip()
        if not param_name or not param_value:
            continue
        line_key = _normalize_cnf_param_name(param_name)
        if line_key in client_written:
            continue
        client_lines.append(f"{param_name}={param_value}")
        client_written.add(line_key)

    config["cnf_sections"] = {
        "mysqld": mysqld_lines,
        "client": client_lines,
    }


def _normalize_cnf_param_name(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")
