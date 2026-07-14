"""MySQL 从库部署执行器。"""
from __future__ import annotations

import json

from apps.dbmgr.deploy_constants import _coerce_bool
from apps.dbmgr.deploy_executors.base import BaseDeployExecutor
from apps.dbmgr.deploy_services import (
    ensure_mysql_replica_job_steps,
    ensure_mysql_replica_master_install,
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
        if step.step_code == "repl_semi_sync":
            ctx = (job.resolved_params or {}).get("context") or {}
            profile = (job.resolved_params or {}).get("profile") or {}
            enable = _coerce_bool(ctx.get("enable_semi_sync"), default=False)
            major = (profile.get("major_version") or "").strip()
            if not enable or major != "5.7":
                return True, "未开启半同步或非 MySQL 5.7，跳过 repl_semi_sync"
            try:
                ensure_mysql_replica_root_credentials(job)
                ensure_mysql_replica_master_install(job)
                job.refresh_from_db()
            except ServiceError as exc:
                return False, str(exc)

            ctx = (job.resolved_params or {}).get("context") or {}
            master_deploy_host_id = ctx.get("master_deploy_host_id")
            if not master_deploy_host_id:
                return False, "缺少主库部署主机 (master_deploy_host_id)，无法配置半同步"

            slave_host_id = int(job.target_host_id)
            master_host_id = int(master_deploy_host_id)
            interpreter_map: dict[int, str] = {}
            for hid in {slave_host_id, master_host_id}:
                ok, msg, interp = self._ensure_host_python_interpreter(job, hid)
                if not ok:
                    return False, f"主机 #{hid} Python 预检失败: {msg}"
                if interp:
                    interpreter_map[hid] = interp

            return self._run_ansible_step(
                job,
                "repl_semi_sync",
                inventory_groups={
                    "targets": [slave_host_id],
                    "master": [master_host_id],
                },
                python_interpreter_by_host_id=interpreter_map,
            )
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

    def _ensure_host_python_interpreter(
        self,
        job: DbDeployJob,
        host_id: int,
    ) -> tuple[bool, str, str | None]:
        """解析指定主机的 Ansible Python 解释器（从库主机复用 executor 缓存）。"""
        if host_id == job.target_host_id:
            ok, msg = self._ensure_python_interpreter(job)
            return ok, msg, self._python_interpreter

        result = dict(job.result or {})
        cache_key = f"ansible_python_interpreter_host_{host_id}"
        saved = result.get(cache_key)
        if saved:
            return True, "从任务记录恢复", str(saved)

        from apps.common.ansible_inventory import ensure_python_interpreter

        ok, msg, interpreter = ensure_python_interpreter(host_id)
        if ok and interpreter:
            result[cache_key] = interpreter
            job.result = result
            job.save(update_fields=["result", "updated_at"])
        return ok, msg, interpreter
