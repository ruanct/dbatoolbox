"""部署执行器注册表。"""
from __future__ import annotations

from .base import BaseDeployExecutor
from .mysql_replica import MysqlReplicaExecutor
from .mysql_standalone import MysqlStandaloneExecutor
from .oracle_standalone import OracleStandaloneExecutor

EXECUTOR_REGISTRY: dict[str, type[BaseDeployExecutor]] = {
    "mysql_standalone": MysqlStandaloneExecutor,
    "mysql_replica": MysqlReplicaExecutor,
    "oracle_standalone": OracleStandaloneExecutor,
}


def get_executor(job_type: str) -> BaseDeployExecutor:
    executor_cls = EXECUTOR_REGISTRY.get(job_type)
    if not executor_cls:
        raise ValueError(f"不支持的部署类型: {job_type}")
    return executor_cls()
