"""MySQL 从库部署执行器。"""
from __future__ import annotations

import json

from apps.dbmgr.deploy_executors.base import BaseDeployExecutor
from apps.dbmgr.deploy_services import (
    ensure_mysql_replica_job_steps,
    ensure_mysql_replica_root_credentials,
    merge_master_runtime_into_job,
    parse_deploy_output_marker,
    register_replica_instance_from_job,
    repair_mysql_replica_slave_server_id,
    save_replication_status,
)
from apps.dbmgr.models import DbDeployJob, DbDeployJobStep
from apps.dbmgr.services import ServiceError


class MysqlReplicaExecutor(BaseDeployExecutor):
    job_type = "mysql_replica"

    def run(self, job_id: int) -> None:
        ensure_mysql_replica_job_steps(job_id)
        super().run(job_id)

    def execute_step(self, job: DbDeployJob, step: DbDeployJobStep) -> tuple[bool, str]:
        if step.step_code == "register_cmdb":
            return self._register_replica_cmdb(job)
        if step.step_code == "configure":
            ok, output = self._run_ansible_step(job, "repl_master_probe")
            if not ok:
                return False, output
            try:
                runtime = parse_deploy_output_marker(output, "MASTER_RUNTIME_JSON")
                merge_master_runtime_into_job(job, runtime)
            except (ServiceError, json.JSONDecodeError) as exc:
                return False, f"解析主库运行参数失败: {exc}"
            job.refresh_from_db()
            return self._run_ansible_step(job, "configure")
        if step.step_code == "verify":
            success, output = self._run_ansible_step(job, "replica_version_check")
            if success:
                version = self.parse_detected_version(output)
                if version:
                    self.save_detected_version(job, version)
            return success, output
        if step.step_code == "repl_setup":
            try:
                ensure_mysql_replica_root_credentials(job)
                repair_mysql_replica_slave_server_id(job)
                job.refresh_from_db()
            except ServiceError as exc:
                return False, str(exc)
            return self._run_ansible_step(job, "repl_setup")
        if step.step_code == "repl_verify":
            try:
                repair_mysql_replica_slave_server_id(job)
                job.refresh_from_db()
            except ServiceError as exc:
                return False, str(exc)
            success, output = self._run_ansible_step(job, "repl_verify")
            if success:
                try:
                    status = parse_deploy_output_marker(output, "REPLICATION_STATUS_JSON")
                    save_replication_status(job, status)
                except (ServiceError, json.JSONDecodeError):
                    pass
            return success, output
        if step.step_code == "precheck":
            return self._run_precheck(job)
        if step.step_code in {"post_config"}:
            return True, "从库部署跳过 post_config"
        return self._run_ansible_step(job, step.step_code)

    def _register_replica_cmdb(self, job: DbDeployJob) -> tuple[bool, str]:
        if job.instance_id:
            return True, f"实例已注册: {job.instance.instance_name}"
        try:
            instance = register_replica_instance_from_job(job)
        except ServiceError as exc:
            return False, str(exc)
        return True, f"已注册从库实例: {instance.instance_name} (ID={instance.id})"
