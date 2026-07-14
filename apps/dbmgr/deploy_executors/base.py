"""部署任务执行器基类。"""
from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.dbmgr.deploy_ansible import run_deploy_playbook_step
from apps.dbmgr.deploy_constants import JOB_TYPE_PLAYBOOK_MAP, resolve_deploy_step_timeout
from apps.dbmgr.deploy_services import (
    mark_job_finished,
    mark_job_running,
    register_instance_from_job,
    update_step_status,
)
from apps.dbmgr.models import DbDeployJob, DbDeployJobStep


class BaseDeployExecutor:
    job_type: str = ""

    def __init__(self) -> None:
        self._python_interpreter: str | None = None

    def run(self, job_id: int) -> None:
        job = (
            DbDeployJob.objects.select_related("target_host")
            .prefetch_related("steps")
            .get(id=job_id)
        )
        steps = list(job.steps.order_by("sort_order", "id"))
        mark_job_running(job, "running")

        for step in steps:
            if step.status == "succeeded":
                continue

            if step.step_code in {"verify"}:
                job.status = "verifying"
                job.save(update_fields=["status", "updated_at"])
            elif step.step_code == "precheck":
                job.status = "prechecking"
                job.save(update_fields=["status", "updated_at"])

            update_step_status(step, status="running")
            try:
                success, output = self.execute_step(job, step)
            except Exception as exc:  # noqa: BLE001
                success, output = False, str(exc)

            update_step_status(step, status="succeeded" if success else "failed", output=output)
            if success and step.step_code == "verify":
                version = self.parse_detected_version(output)
                if version:
                    self.save_detected_version(job, version)
            if not success:
                mark_job_finished(job, success=False, error_message=output[:512])
                return

        mark_job_finished(job, success=True)

    def execute_step(self, job: DbDeployJob, step: DbDeployJobStep) -> tuple[bool, str]:
        if step.step_code == "register_cmdb":
            return self._register_cmdb(job)
        if step.step_code == "precheck":
            return self._run_precheck(job)
        return self._run_ansible_step(job, step.step_code)

    def _ensure_python_interpreter(self, job: DbDeployJob) -> tuple[bool, str]:
        """续跑时 precheck 已跳过，仍需在首次 Ansible 调用前解析解释器。"""
        if self._python_interpreter:
            return True, "已缓存"

        saved = (job.result or {}).get("ansible_python_interpreter")
        if saved:
            self._python_interpreter = str(saved)
            return True, "从任务记录恢复"

        from apps.common.ansible_inventory import ensure_python_interpreter

        ok, message, interpreter = ensure_python_interpreter(job.target_host_id)
        if not ok:
            return False, message
        self._python_interpreter = interpreter
        return True, message

    def _persist_python_interpreter(self, job: DbDeployJob) -> None:
        if not self._python_interpreter:
            return
        result = dict(job.result or {})
        if result.get("ansible_python_interpreter") == self._python_interpreter:
            return
        result["ansible_python_interpreter"] = self._python_interpreter
        job.result = result
        job.save(update_fields=["result", "updated_at"])

    def _run_precheck(self, job: DbDeployJob) -> tuple[bool, str]:
        ok, message = self._ensure_python_interpreter(job)
        if not ok:
            return False, message
        interpreter = self._python_interpreter or ""

        success, ansible_output = self._run_ansible_step(job, "precheck")
        probe_line = f"[Python 预检] ansible_python_interpreter={interpreter} ({message})"
        if success:
            self._persist_python_interpreter(job)
            return True, probe_line + ("\n" + ansible_output if ansible_output else "")
        return False, probe_line + ("\n" + ansible_output if ansible_output else "")

    def _run_ansible_step(
        self,
        job: DbDeployJob,
        step_code: str,
        *,
        inventory_groups: dict[str, list[int]] | None = None,
        python_interpreter_by_host_id: dict[int, str] | None = None,
    ) -> tuple[bool, str]:
        ok, message = self._ensure_python_interpreter(job)
        if not ok:
            return False, message
        self._persist_python_interpreter(job)

        playbook = JOB_TYPE_PLAYBOOK_MAP.get(job.job_type)
        if not playbook:
            return False, f"未配置 Playbook: {job.job_type}"
        deploy_vars = dict(job.resolved_params or {})
        deploy_vars["credentials"] = job.resolved_params.get("credentials") or {}
        interpreter_map = dict(python_interpreter_by_host_id or {})
        if self._python_interpreter and job.target_host_id not in interpreter_map:
            interpreter_map[job.target_host_id] = self._python_interpreter
        return run_deploy_playbook_step(
            host_id=job.target_host_id,
            playbook_relative=playbook,
            step_tag=step_code,
            deploy_vars=deploy_vars,
            python_interpreter=self._python_interpreter,
            inventory_groups=inventory_groups,
            python_interpreter_by_host_id=interpreter_map or None,
            timeout=resolve_deploy_step_timeout(step_code),
        )

    def _register_cmdb(self, job: DbDeployJob) -> tuple[bool, str]:
        if job.instance_id:
            return True, f"实例已注册: {job.instance.instance_name}"
        instance = register_instance_from_job(job)
        return True, f"已注册实例: {instance.instance_name} (ID={instance.id})"

    def save_detected_version(self, job: DbDeployJob, version: str) -> None:
        result = dict(job.result or {})
        result["detected_version"] = version[:32]
        result["verified_at"] = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
        job.result = result
        job.save(update_fields=["result", "updated_at"])

    def parse_detected_version(self, output: str) -> str:
        for line in output.splitlines():
            if "MySQL version=" in line:
                return line.split("=", 1)[1].strip()
            text = line.strip()
            if "Oracle Database" in text:
                return text[:32]
        return ""
