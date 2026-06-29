"""部署任务执行器基类。"""
from __future__ import annotations

from typing import Any

from django.utils import timezone

from apps.dbmgr.deploy_ansible import run_deploy_playbook_step
from apps.dbmgr.deploy_constants import JOB_TYPE_PLAYBOOK_MAP
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

    def _run_precheck(self, job: DbDeployJob) -> tuple[bool, str]:
        from apps.common.ansible_inventory import ensure_python_interpreter

        ok, message, interpreter = ensure_python_interpreter(job.target_host_id)
        if not ok:
            return False, message
        self._python_interpreter = interpreter

        success, ansible_output = self._run_ansible_step(job, "precheck")
        probe_line = f"[Python 预检] ansible_python_interpreter={interpreter} ({message})"
        if success:
            return True, probe_line + ("\n" + ansible_output if ansible_output else "")
        return False, probe_line + ("\n" + ansible_output if ansible_output else "")

    def _run_ansible_step(self, job: DbDeployJob, step_code: str) -> tuple[bool, str]:
        playbook = JOB_TYPE_PLAYBOOK_MAP.get(job.job_type)
        if not playbook:
            return False, f"未配置 Playbook: {job.job_type}"
        deploy_vars = dict(job.resolved_params or {})
        deploy_vars["credentials"] = job.resolved_params.get("credentials") or {}
        return run_deploy_playbook_step(
            host_id=job.target_host_id,
            playbook_relative=playbook,
            step_tag=step_code,
            deploy_vars=deploy_vars,
            python_interpreter=self._python_interpreter,
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
